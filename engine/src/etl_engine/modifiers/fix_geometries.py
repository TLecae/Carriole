"""FixGeometries : rend valides les géométries invalides (auto-intersection, anneau non fermé…)."""

from __future__ import annotations

from typing import Literal

import numpy as np
import pyarrow as pa
import shapely
from pydantic import Field

from etl_engine.data import GEOM, geometries, get_crs, replace_geometries, with_crs
from etl_engine.sdk import GEOMETRY_SLICE, REJECTED, ConfigError, Modifier, Params, reject
from etl_engine.spatial import keep_dimension


class FixGeometries(Modifier):
    name = "FixGeometries"
    label = "Réparer les géométries"
    category = "Géométrie"
    slice_rows = GEOMETRY_SLICE
    description = (
        "Rend valides les géométries invalides (auto-intersection, anneau non fermé…) ; les autres sont intactes."
    )
    help = (
        "Toutes les entités ressortent sur « output » avec une géométrie valide. Les géométries déjà valides ne "
        "sont pas touchées. Les autres sont corrigées (GEOS make_valid) : un polygone en « nœud papillon » devient un "
        "multipolygone de deux triangles, un anneau mal fermé est refermé. "
        "« Garder le type » écarte les morceaux dégénérés que la réparation peut produire (un segment ou un point "
        "né d'un polygone aplati) : le polygone reste un polygone. "
        "« Rejected » reçoit, avec le motif, les entités sans géométrie, à géométrie vide ou qui ne donnent plus rien "
        "une fois réparées. Pour seulement contrôler sans modifier, utiliser « Valider les géométries »."
    )

    class Params(Params):
        method: Literal["linework", "structure"] = Field(
            "linework",
            title="Méthode",
            description=(
                "linework : les lignes sont découpées aux intersections (comportement classique, rapide). "
                "structure : reconstruit les surfaces d'après leurs anneaux (trous mieux respectés, plus lent)."
            ),
        )
        keep_type: bool = Field(
            True,
            title="Garder le type de géométrie",
            description="Écarte les morceaux de dimension inférieure (lignes ou points nés d'un polygone aplati).",
        )
        report_attribute: str = Field(
            "_repaired_reason",
            title="Attribut du motif de réparation",
            description=(
                "Reçoit le défaut d'origine pour les géométries réparées, vide pour les autres. Vide = pas d'attribut."
            ),
        )

    def output_schema(self, inputs):
        schema = inputs.get("input")
        if schema is None:
            return {"output": None}
        if GEOM not in schema.names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        report = self.params.report_attribute.strip()
        if report and report not in schema.names:
            schema = schema.append(pa.field(report, pa.string()))
        return {"output": schema}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        if GEOM not in batch.column_names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        p = self.params
        geoms = geometries(batch)
        n = len(geoms)
        missing = shapely.is_missing(geoms)
        empty = shapely.is_empty(geoms)
        bad = ~missing & ~empty & ~shapely.is_valid(geoms)

        out = np.array(geoms, dtype=object)
        reasons = np.full(n, None, dtype=object)  # motif d'origine des géométries réparées
        rejected = np.full(n, None, dtype=object)  # motif de rejet
        rejected[missing] = "pas de géométrie"
        rejected[empty] = "géométrie vide"
        if bad.any():
            where = np.flatnonzero(bad)
            before = shapely.is_valid_reason(geoms[where])
            fixed = shapely.make_valid(geoms[where], method=p.method)
            if p.keep_type:
                fixed = keep_dimension(fixed, shapely.get_dimensions(geoms[where]))
            lost = shapely.is_missing(fixed) | shapely.is_empty(fixed) | ~shapely.is_valid(fixed)
            rejected[where[lost]] = np.char.add("irréparable : ", before[lost].astype(str)).astype(object)
            out[where[~lost]] = fixed[~lost]
            reasons[where[~lost]] = before[~lost]

        crs = get_crs(batch)
        repaired = np.zeros(n, dtype=bool)
        if bad.any():
            repaired[where[~lost]] = True  # seules ces géométries ont changé
        table = replace_geometries(batch, out, repaired)
        report = p.report_attribute.strip()
        if report:
            values = pa.array(reasons, pa.string()) if repaired.any() else pa.nulls(n, pa.string())
            if report in table.column_names:
                table = table.set_column(table.column_names.index(report), report, values)
            else:
                table = table.append_column(report, values)
        refused = missing | empty
        if bad.any():
            refused[where[lost]] = True
        result: dict[str, pa.Table] = {"output": with_crs(table.filter(pa.array(~refused)), crs)}
        if refused.any():
            result[REJECTED] = with_crs(
                reject(table.filter(pa.array(refused)), pa.array(rejected[refused], pa.string())), crs
            )
        return result
