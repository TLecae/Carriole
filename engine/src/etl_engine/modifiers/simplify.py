"""Simplify : généralise les géométries (Douglas-Peucker)."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import shapely
from pydantic import Field

from etl_engine.data import GEOM, geometries, get_crs, replace_geometries
from etl_engine.sdk import GEOMETRY_SLICE, ConfigError, Modifier, Params


class Simplify(Modifier):
    name = "Simplify"
    label = "Simplifier les géométries"
    category = "Géométrie"
    slice_rows = GEOMETRY_SLICE
    description = "Réduit le nombre de sommets en gardant la forme à la tolérance près (Douglas-Peucker)."
    help = (
        "La tolérance est dans l'unité du système de coordonnées. « Préserver la topologie » évite les "
        "auto-intersections et les polygones qui disparaissent (un peu plus lent)."
    )
    rejected = False

    class Params(Params):
        tolerance: float = Field(
            1.0,
            gt=0,
            title="Tolérance",
            description="Écart maximal toléré, dans l'unité du système de coordonnées (mètres en Lambert-93).",
        )
        preserve_topology: bool = Field(
            True,
            title="Préserver la topologie",
            description="Évite les auto-intersections et les polygones qui disparaissent (un peu plus lent).",
        )

    def output_schema(self, inputs):
        schema = inputs.get("input")
        if schema is not None and GEOM not in schema.names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        return {"output": schema}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        if GEOM not in batch.column_names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        geoms = geometries(batch)
        present = ~shapely.is_missing(geoms)
        out = np.array(geoms, dtype=object)
        out[present] = shapely.simplify(
            geoms[present], self.params.tolerance, preserve_topology=self.params.preserve_topology
        )
        changed = present & (shapely.get_num_coordinates(out) != shapely.get_num_coordinates(geoms))
        return {"output": replace_geometries(batch, out, changed, get_crs(batch))}
