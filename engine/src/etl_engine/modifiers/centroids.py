"""Centroids : remplace la géométrie par un point caractéristique."""

from __future__ import annotations

from typing import Literal

import pyarrow as pa
import shapely
from pydantic import Field

from etl_engine.data import GEOM, geometries, get_crs, present, replace_geometries
from etl_engine.sdk import GEOMETRY_SLICE, REJECTED, ConfigError, Modifier, Params, reject


class Centroids(Modifier):
    name = "Centroids"
    label = "Remplacer par un point central"
    category = "Géométrie"
    slice_rows = GEOMETRY_SLICE
    description = "Remplace chaque géométrie par un point : centre de gravité, point intérieur ou centre de l'emprise."
    help = (
        "« centroid » : centre de gravité (peut tomber hors d'un polygone en forme de croissant) ; "
        "« point_on_surface » : un point toujours à l'intérieur ; « bbox_center » : centre de l'emprise. "
        "Les entités sans géométrie ou à géométrie vide partent sur « Rejected »."
    )

    class Params(Params):
        method: Literal["centroid", "point_on_surface", "bbox_center"] = Field(
            "centroid",
            title="Méthode",
            description=(
                "centroid : centre de gravité (peut sortir d'un polygone en croissant) ; point_on_surface : un "
                "point toujours à l'intérieur ; bbox_center : centre de l'emprise."
            ),
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
        ok = present(geoms)
        sub = geoms[ok]
        if self.params.method == "centroid":
            points = shapely.centroid(sub)
        elif self.params.method == "point_on_surface":
            points = shapely.point_on_surface(sub)
        else:
            points = shapely.centroid(shapely.envelope(sub))
        result = {"output": replace_geometries(batch.filter(pa.array(ok)), points, crs=get_crs(batch))}
        if not ok.all():
            result[REJECTED] = reject(batch.filter(pa.array(~ok)), "pas de géométrie ou géométrie vide")
        return result
