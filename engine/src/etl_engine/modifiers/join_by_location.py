"""JoinByLocation : compte (et liste) les entités liées spatialement à chaque entité."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pyarrow as pa
import shapely
from pydantic import Field

from etl_engine.blocking import collect
from etl_engine.data import GEOM, geometries, get_crs, with_crs
from etl_engine.sdk import (
    REJECTED,
    Attribute,
    ConfigError,
    Emit,
    Modifier,
    Params,
    ordered_map,
    reject,
)
from etl_engine.spatial import Predicate, SpatialIndex, check_same_crs


class JoinByLocation(Modifier):
    name = "JoinByLocation"
    label = "Relateur spatial"
    category = "Jointures"
    description = "Ajoute à chaque entité « base » le nombre d'entités « related » en relation avec elle."
    help = (
        "Pour chaque entité de « base », on compte les entités de « related » qui vérifient la relation "
        "(base <relation> related). Option : lister les valeurs d'un attribut des entités liées (séparées par "
        "un séparateur, sans doublon, triées). Les entités sans géométrie partent sur « Rejected »."
    )
    mode = "blocking"
    inputs = ("base", "related")
    outputs = ("output",)

    class Params(Params):
        predicate: Predicate = Field("intersects", title="Relation", description="base <relation> related.")
        distance: float = Field(
            0.0,
            title="Distance (dwithin)",
            description="Distance maximale pour la relation dwithin, dans l'unité du système de coordonnées.",
        )
        count_attribute: str = Field(
            "_related_count",
            title="Attribut du nombre d'entités liées",
            description="Nom de l'attribut qui reçoit le nombre d'entités de « related » vérifiant la relation.",
        )
        list_attribute: Attribute = Field(
            "", title="Lister cet attribut des entités liées", description="Vide = pas de liste."
        )
        list_name: str = Field(
            "_related_values",
            title="Nom de l'attribut de liste",
            description="Nom de l'attribut qui reçoit la liste (si « Lister cet attribut » est renseigné).",
        )
        separator: str = Field(", ", title="Séparateur", description="Texte placé entre deux valeurs de la liste.")

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        base, related = inputs.get("base"), inputs.get("related")
        if base is None:
            return {"output": None}
        out = base
        if self.params.count_attribute and self.params.count_attribute not in out.names:
            out = out.append(pa.field(self.params.count_attribute, pa.int64()))
        if self.params.list_attribute and self.params.list_name not in out.names:
            out = out.append(pa.field(self.params.list_name, pa.string()))
        if related is not None:
            check_same_crs(base, related)
        return {"output": out}

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        # « related » est lu et indexé une fois ; les entités « base » passent lot par lot, plusieurs lots à la fois
        p = self.params
        related = collect(inputs.get("related", ()))
        index = values = None
        if related is not None and related.num_rows:
            if GEOM not in related.column_names:
                raise ConfigError("les entités « related » n'ont pas de géométrie")
            if p.list_attribute and p.list_attribute not in related.column_names:
                raise ConfigError(f"attribut absent de « related » : {p.list_attribute}")
            index = SpatialIndex(related)
            if p.list_attribute:
                values = related.column(p.list_attribute)

        def relate(base: pa.Table) -> list[tuple[str, pa.Table]]:
            if GEOM not in base.column_names:
                raise ConfigError("les entités « base » n'ont pas de géométrie")
            geoms = geometries(base)
            missing = shapely.is_missing(geoms)
            counts = np.zeros(base.num_rows, dtype=np.int64)
            bi = rj = np.array([], dtype=np.int64)
            if index is not None:
                check_same_crs(base, related)
                bi, rj = index.pairs(geoms, p.predicate, p.distance)
                counts = np.bincount(bi, minlength=base.num_rows).astype(np.int64)
            out = base
            if p.count_attribute:
                out = _put(out, p.count_attribute, pa.array(counts))
            if p.list_attribute:
                out = _put(out, p.list_name, _lists(base.num_rows, bi, rj, values, p.separator))
            out = with_crs(out, get_crs(base))
            result = [("output", out.filter(pa.array(~missing)))]
            if missing.any():
                result.append((REJECTED, reject(base.filter(pa.array(missing)), "pas de géométrie")))
            return result

        for result in ordered_map(relate, inputs.get("base", ()), self.ctx.workers, self.ctx):
            for port, table in result:
                emit(port, table)


def _lists(n: int, bi: np.ndarray, rj: np.ndarray, values: pa.ChunkedArray | None, separator: str) -> pa.Array:
    """Pour chaque entité base : valeurs distinctes non nulles des entités liées, triées et jointes (None si aucune)."""
    texts: list[str | None] = [None] * n
    if values is None or not len(bi):
        return pa.array(texts, pa.string())
    taken = values.take(pa.array(rj, pa.int64())).to_pylist()
    bounds = np.flatnonzero(np.diff(bi)) + 1  # bi est trié : une tranche par entité base
    for start, stop in zip(np.r_[0, bounds], np.r_[bounds, len(bi)], strict=True):
        found = {str(v) for v in taken[start:stop] if v is not None}
        if found:
            texts[int(bi[start])] = separator.join(sorted(found))
    return pa.array(texts, pa.string())


def _put(table: pa.Table, name: str, column: pa.Array) -> pa.Table:
    if name in table.column_names:
        return table.set_column(table.column_names.index(name), name, column)
    return table.append_column(name, column)
