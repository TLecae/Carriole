"""SplitByGeometryType : route les entités selon le type de leur géométrie."""

from __future__ import annotations

import numpy as np
import pyarrow as pa

from etl_engine.data import AREAS, GEOM, LINES, POINTS, geometries, present, type_ids
from etl_engine.sdk import GEOMETRY_SLICE, ConfigError, Modifier


class SplitByGeometryType(Modifier):
    name = "SplitByGeometryType"
    label = "Filtre par type de géométrie"
    category = "Filtres"
    slice_rows = GEOMETRY_SLICE
    description = "Sépare les entités par type de géométrie : points, lignes, surfaces, collections, sans géométrie."
    help = (
        "Les multi-géométries suivent leur famille (MultiPolygon → Area). Une géométrie vide compte comme "
        "« NoGeometry »."
    )
    outputs = ("Point", "Line", "Area", "Collection", "NoGeometry")
    rejected = False

    def output_schema(self, inputs):
        schema = inputs.get("input")
        if schema is not None and GEOM not in schema.names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        return {name: schema for name in self.outputs}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        if GEOM not in batch.column_names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        geoms = geometries(batch)
        kinds = np.full(len(geoms), "Collection", dtype=object)
        ids = type_ids(geoms)
        kinds[np.isin(ids, POINTS)] = "Point"
        kinds[np.isin(ids, LINES)] = "Line"
        kinds[np.isin(ids, AREAS)] = "Area"
        kinds[~present(geoms)] = "NoGeometry"
        return {name: batch.filter(pa.array(kinds == name)) for name in self.outputs}
