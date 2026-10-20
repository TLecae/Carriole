"""Aggregate : regroupe les entités par clé en une entité agrégée."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import numpy as np
import pyarrow as pa
import shapely
from pydantic import Field

from etl_engine.blocking import collect, group_codes, group_slices
from etl_engine.data import GEOM, geometries, get_crs, with_crs
from etl_engine.sdk import AttributeList, ConfigError, Emit, Modifier, Params

_MULTI = {0: shapely.multipoints, 1: shapely.multilinestrings, 3: shapely.multipolygons}


def collect_groups(geoms: np.ndarray, groups: np.ndarray, count: int) -> np.ndarray:
    """Pour chaque groupe, ses géométries réunies sans être fusionnées : Multi… si toutes leurs parties sont de même
    famille (point, ligne, polygone), collection sinon ; None si le groupe n'a pas de géométrie. Vectorisé.

    Les Multi… et collections sont aplaties d'un niveau. `groups` : numéro de groupe de chaque géométrie.
    """
    out = np.full(count, None, dtype=object)
    keep = ~shapely.is_missing(geoms)
    order = np.argsort(groups[keep], kind="stable")  # les parties de chaque groupe, dans l'ordre d'arrivée
    parts, index = shapely.get_parts(geoms[keep][order], return_index=True)
    owner = groups[keep][order][index]
    if not len(parts):
        return out
    kinds = shapely.get_type_id(parts)
    low = np.full(count, 99)
    high = np.full(count, -1)
    np.minimum.at(low, owner, kinds)
    np.maximum.at(high, owner, kinds)
    same = (low == high) & np.isin(low, list(_MULTI))
    for kind, build in _MULTI.items():  # groupes d'une seule famille : un Multi… par groupe
        sel = same[owner] & (kinds == kind)
        if sel.any():
            ids, local = np.unique(owner[sel], return_inverse=True)
            out[ids] = build(parts[sel], indices=local)
    mixed = ~same[owner]
    if mixed.any():
        ids, local = np.unique(owner[mixed], return_inverse=True)
        out[ids] = shapely.geometrycollections(parts[mixed], indices=local)
    return out


class Aggregate(Modifier):
    name = "Aggregate"
    label = "Agréger"
    category = "Calculs"
    description = "Regroupe les entités par clé : une entité par groupe, avec le nombre d'entités et des sommes."
    help = (
        "Les attributs de l'entité agrégée sont ceux de la première entité du groupe. Géométrie : « collect » "
        "réunit les géométries sans les fusionner (MultiPolygon, MultiPoint… ou collection hétérogène), « first » "
        "garde celle de la première entité, « none » la retire. Pour fusionner réellement les polygones, "
        "utiliser le Dissolve."
    )
    mode = "blocking"
    outputs = ("output",)
    rejected = False

    class Params(Params):
        group_by: AttributeList = Field([], title="Regrouper par", description="Vide = tout dans un seul groupe.")
        count_attribute: str = Field(
            "_count", title="Attribut du nombre d'entités", description="Vide = pas de comptage."
        )
        sum_attributes: AttributeList = Field([], title="Attributs à additionner", description="Créent `attribut_sum`.")
        geometry: Literal["collect", "first", "none"] = Field(
            "collect",
            title="Géométrie",
            description=(
                "collect : réunit les géométries du groupe sans les fusionner ; first : celle de la première "
                "entité ; none : aucune géométrie."
            ),
        )

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        schema = inputs.get("input")
        if schema is None:
            return {"output": None}
        return {"output": self._aggregate(schema.empty_table()).schema}

    def _aggregate(self, table: pa.Table) -> pa.Table:
        p = self.params
        missing = [a for a in [*p.group_by, *p.sum_attributes] if a not in table.column_names]
        if missing:
            raise ConfigError(f"attributs absents : {missing}")
        n = table.num_rows
        codes, count = group_codes(table, list(p.group_by))
        order, bounds = group_slices(codes, count)
        first = order[bounds[:-1]]
        out = table.take(pa.array(first, pa.int64()))
        has_geom = GEOM in table.column_names
        if has_geom:
            if p.geometry == "none":
                out = out.drop_columns([GEOM])
            elif p.geometry == "collect":
                geoms = geometries(table) if n else np.array([], dtype=object)
                merged = collect_groups(geoms, codes, count)
                out = out.set_column(out.column_names.index(GEOM), GEOM, pa.array(shapely.to_wkb(merged), pa.binary()))
        if p.count_attribute:
            out = _put(out, p.count_attribute, pa.array(np.diff(bounds), pa.int64()))
        for attr in p.sum_attributes:
            values = table.column(attr).cast(pa.float64(), safe=False).to_numpy(zero_copy_only=False) if n else []
            values = np.nan_to_num(np.asarray(values, dtype=float)[order], nan=0.0)
            sums = np.add.reduceat(values, bounds[:-1]) if count else np.array([], dtype=float)
            out = _put(out, f"{attr}_sum", pa.array(sums, pa.float64()))
        return with_crs(out, get_crs(table))

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        table = collect(inputs.get("input", ()))
        if table is None or table.num_rows == 0:
            return
        result = self._aggregate(table)
        emit("output", result)
        self.ctx.info(f"{table.num_rows} entités agrégées en {result.num_rows}")


def _put(table: pa.Table, name: str, column: pa.Array) -> pa.Table:
    if name in table.column_names:
        return table.set_column(table.column_names.index(name), name, column)
    return table.append_column(name, column)
