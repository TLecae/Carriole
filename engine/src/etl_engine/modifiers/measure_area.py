"""MeasureArea : surface et périmètre des polygones."""

from __future__ import annotations

from typing import Literal

import numpy as np
import pyarrow as pa
import pyproj
import shapely
from pydantic import Field

from etl_engine.data import AREAS, GEOM, geometries, get_crs, is_geographic, type_ids, with_crs
from etl_engine.sdk import GEOMETRY_SLICE, REJECTED, ConfigError, Modifier, Params, reject

_FACTOR = {"m2": 1.0, "ha": 1e-4, "km2": 1e-6}
_LENGTH = {"m": 1.0, "km": 1e-3}


class MeasureArea(Modifier):
    name = "MeasureArea"
    label = "Surface et périmètre"
    category = "Géométrie"
    slice_rows = GEOMETRY_SLICE
    description = "Ajoute la surface (et au choix le périmètre) de chaque polygone."
    help = (
        "« planar » calcule dans l'unité du système de coordonnées (des mètres en Lambert-93) ; refusé pour un "
        "système géographique (degrés). « ellipsoidal » calcule sur l'ellipsoïde WGS84, quel que soit le système "
        "(les coordonnées doivent alors être en longitude/latitude). Les entités qui ne sont pas des polygones "
        "partent sur « Rejected »."
    )

    class Params(Params):
        area: str = Field("_area", title="Attribut surface", description="Vide = pas de surface.")
        perimeter: str = Field("", title="Attribut périmètre", description="Vide = pas de périmètre.")
        area_unit: Literal["m2", "ha", "km2"] = Field(
            "m2",
            title="Unité de surface",
            description="Unité de la surface écrite : m² (m2), hectares (ha) ou km² (km2).",
        )
        length_unit: Literal["m", "km"] = Field(
            "m", title="Unité de périmètre", description="Unité du périmètre écrit : mètres ou kilomètres."
        )
        method: Literal["planar", "ellipsoidal"] = Field(
            "planar",
            title="Méthode",
            description=(
                "planar : dans l'unité du système de coordonnées (refusé en degrés) ; ellipsoidal : sur "
                "l'ellipsoïde WGS84, les coordonnées doivent être en longitude/latitude."
            ),
        )
        decimals: int = Field(2, ge=0, le=15, title="Décimales", description="Nombre de décimales conservées (0 à 15).")

    def _fields(self) -> list[str]:
        out = [n for n in (self.params.area, self.params.perimeter) if n]
        if not out:
            raise ConfigError("choisir au moins la surface ou le périmètre")
        return out

    def output_schema(self, inputs):
        names = self._fields()
        schema = inputs.get("input")
        if schema is None:
            return {"output": None}
        if GEOM not in schema.names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        if self.params.method == "planar" and is_geographic(crs := get_crs(schema)):
            raise ConfigError(f"{crs} est géographique (degrés) : reprojeter d'abord, ou choisir « ellipsoidal »")
        out = schema
        for n in names:
            f = pa.field(n, pa.float64())
            out = out.set(out.get_field_index(n), f) if n in out.names else out.append(f)
        return {"output": out}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        p = self.params
        self._fields()
        if GEOM not in batch.column_names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        if p.method == "planar" and is_geographic(crs := get_crs(batch)):
            raise ConfigError(f"{crs} est géographique (degrés) : reprojeter d'abord, ou choisir « ellipsoidal »")
        geoms = geometries(batch)
        ok = np.isin(type_ids(geoms), AREAS)
        area = np.full(len(geoms), np.nan)
        perimeter = np.full(len(geoms), np.nan)
        if p.method == "planar":
            area[ok] = shapely.area(geoms[ok])
            perimeter[ok] = shapely.length(geoms[ok])  # périmètre d'un polygone = longueur de son contour
        else:
            geod = pyproj.Geod(ellps="WGS84")
            for i in np.flatnonzero(ok):
                a, per = geod.geometry_area_perimeter(geoms[i])
                area[i], perimeter[i] = abs(a), per
        area = np.round(area * _FACTOR[p.area_unit], p.decimals)
        perimeter = np.round(perimeter * _LENGTH[p.length_unit], p.decimals)
        out = batch
        for name, values in ((p.area, area), (p.perimeter, perimeter)):
            if not name:
                continue
            column = pa.array(values, pa.float64(), mask=~ok)
            out = (
                out.set_column(out.column_names.index(name), name, column)
                if name in out.column_names
                else out.append_column(name, column)
            )
        result = {"output": with_crs(out.filter(pa.array(ok)), get_crs(batch))}
        if not ok.all():
            bad = ~ok
            reasons = np.where(shapely.is_missing(geoms[bad]), "pas de géométrie", "pas de polygone")
            result[REJECTED] = reject(batch.filter(pa.array(bad)), pa.array(reasons, pa.string()))
        return result
