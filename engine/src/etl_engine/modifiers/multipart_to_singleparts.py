"""MultipartToSingleparts : une entité par partie d'une géométrie multiple."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import shapely
from pydantic import Field

from etl_engine.data import GEOM, geometries, get_crs, replace_geometries, type_ids, with_crs
from etl_engine.sdk import GEOMETRY_SLICE, ConfigError, Modifier, Params

MULTI = (4, 5, 6, 7)  # MultiPoint, MultiLineString, MultiPolygon, GeometryCollection


def _explode(geoms: np.ndarray, recursive: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(ligne d'origine, géométrie, éclatée ?) de chaque entité de sortie, dans l'ordre des lignes puis des parties.

    Une géométrie simple, vide ou absente donne une entité, sa géométrie d'origine ; une multiple, une par partie
    (et, si `recursive`, une par partie des collections imbriquées). Tout est vectorisé (`shapely.get_parts`).
    """
    multi = np.isin(type_ids(geoms), MULTI) & ~shapely.is_empty(geoms)
    owners = np.flatnonzero(multi)
    parts, index = shapely.get_parts(geoms[multi], return_index=True)
    owner = owners[index]
    while recursive and len(parts):
        nested = np.isin(type_ids(parts), MULTI) & ~shapely.is_empty(parts)
        if not nested.any():
            break
        sub, sub_index = shapely.get_parts(parts[nested], return_index=True)
        # chaque partie imbriquée est remplacée, à sa place, par ses propres parties
        key = np.concatenate([np.flatnonzero(~nested), np.flatnonzero(nested)[sub_index]])
        order = np.argsort(key, kind="stable")
        parts = np.concatenate([parts[~nested], sub])[order]
        owner = np.concatenate([owner[~nested], owner[nested][sub_index]])[order]
    keep = np.flatnonzero(~multi)
    rows = np.concatenate([keep, owner])
    order = np.argsort(rows, kind="stable")
    out = np.concatenate([geoms[keep], parts])[order]
    split = np.concatenate([np.zeros(len(keep), dtype=bool), np.ones(len(parts), dtype=bool)])[order]
    return rows[order].astype(np.int64), out, split


def _ranks(rows: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(rang de la partie, nombre de parties) pour des lignes triées : 0, 1, 2… dans chaque suite de lignes égales."""
    if not len(rows):
        return rows, rows
    starts = np.r_[0, np.flatnonzero(np.diff(rows)) + 1]
    sizes = np.diff(np.r_[starts, len(rows)])
    return np.arange(len(rows)) - np.repeat(starts, sizes), np.repeat(sizes, sizes)


class MultipartToSingleparts(Modifier):
    name = "MultipartToSingleparts"
    label = "Éclater les géométries multiples"
    category = "Géométrie"
    slice_rows = GEOMETRY_SLICE
    description = "Sépare chaque géométrie multiple (MultiPolygon, MultiLineString…) en une entité par partie."
    help = (
        "Une entité dont la géométrie a N parties donne N entités qui recopient les autres attributs. Les géométries "
        "simples, vides ou absentes passent telles quelles. « Éclater aussi les collections imbriquées » descend dans "
        "les GeometryCollection qui contiennent d'autres collections. « Attribut du numéro » reçoit le rang de la partie "
        "(à partir de 0) et « Attribut du nombre » le nombre de parties de l'entité d'origine."
    )
    rejected = False

    class Params(Params):
        part_attribute: str = Field(
            "_part", title="Attribut du numéro", description="Rang de la partie, de 0 ; vide = aucun."
        )
        count_attribute: str = Field(
            "", title="Attribut du nombre", description="Nombre de parties de l'entité d'origine ; vide = aucun."
        )
        recursive: bool = Field(
            True,
            title="Éclater aussi les collections imbriquées",
            description="Descend dans les collections de collections.",
        )

    def _added(self) -> list[str]:
        return [n for n in (self.params.part_attribute, self.params.count_attribute) if n]

    def output_schema(self, inputs):
        schema = inputs.get("input")
        if schema is None:
            return {"output": None}
        if GEOM not in schema.names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        out = schema
        for name in self._added():
            field = pa.field(name, pa.int64())
            out = out.set(out.get_field_index(name), field) if name in out.names else out.append(field)
        return {"output": out}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        if GEOM not in batch.column_names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        geoms = geometries(batch)
        rows, parts, split = _explode(geoms, self.params.recursive)
        ranks, counts = _ranks(rows)
        out = replace_geometries(batch.take(pa.array(rows)), parts, split, get_crs(batch))
        for name, values in ((self.params.part_attribute, ranks), (self.params.count_attribute, counts)):
            if name:
                column = pa.array(values, pa.int64())
                out = (
                    out.set_column(out.column_names.index(name), name, column)
                    if name in out.column_names
                    else out.append_column(name, column)
                )
        return {"output": with_crs(out, get_crs(batch))}
