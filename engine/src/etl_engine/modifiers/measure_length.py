"""MeasureLength : longueur des lignes."""

from __future__ import annotations

from typing import Literal

import numpy as np
import pyarrow as pa
import pyproj
import shapely
from pydantic import Field

from etl_engine.data import GEOM, geometries, get_crs, is_geographic, type_ids, with_crs
from etl_engine.sdk import GEOMETRY_SLICE, REJECTED, ConfigError, Modifier, Params, reject

_UNIT = {"m": 1.0, "km": 1e-3}
LINES = (1, 5)  # LineString, MultiLineString


class MeasureLength(Modifier):
    name = "MeasureLength"
    label = "Longueur de lignes"
    category = "Géométrie"
    slice_rows = GEOMETRY_SLICE
    description = "Ajoute la longueur de chaque ligne."
    help = (
        "« planar » calcule dans l'unité du système de coordonnées (des mètres en Lambert-93) ; refusé pour un système "
        "géographique (degrés). « ellipsoidal » calcule sur l'ellipsoïde WGS84, quel que soit le système (les coordonnées "
        "doivent alors être en longitude/latitude). Les entités qui ne sont pas des lignes (points, polygones, sans "
        "géométrie) partent sur « Rejected » ; pour le périmètre d'un polygone, utiliser « Surface et périmètre »."
    )

    class Params(Params):
        attribute: str = Field("_length", title="Attribut longueur", description="Reçoit la longueur.")
        unit: Literal["m", "km"] = Field("m", title="Unité", description="Mètres ou kilomètres.")
        method: Literal["planar", "ellipsoidal"] = Field(
            "planar",
            title="Méthode",
            description="planar : dans l'unité du système de coordonnées (refusé en degrés) ; ellipsoidal : sur l'ellipsoïde WGS84, coordonnées en longitude/latitude.",
        )
        decimals: int = Field(2, ge=0, le=15, title="Décimales", description="Nombre de décimales conservées (0 à 15).")

    def _check(self) -> None:
        if not self.params.attribute:
            raise ConfigError("attribut longueur non renseigné")

    def output_schema(self, inputs):
        self._check()
        schema = inputs.get("input")
        if schema is None:
            return {"output": None}
        if GEOM not in schema.names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        name, field = self.params.attribute, pa.field(self.params.attribute, pa.float64())
        return {
            "output": schema.set(schema.get_field_index(name), field) if name in schema.names else schema.append(field)
        }

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        p = self.params
        self._check()
        if GEOM not in batch.column_names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        if p.method == "planar" and is_geographic(crs := get_crs(batch)):
            raise ConfigError(f"{crs} est géographique (degrés) : reprojeter d'abord, ou choisir « ellipsoidal »")
        geoms = geometries(batch)
        ok = np.isin(type_ids(geoms), LINES)
        length = np.full(len(geoms), np.nan)
        if p.method == "planar":
            length[ok] = shapely.length(geoms[ok])
        else:
            geod = pyproj.Geod(ellps="WGS84")
            for i in np.flatnonzero(ok):
                length[i] = geod.geometry_length(geoms[i])
        column = pa.array(np.round(length * _UNIT[p.unit], p.decimals), pa.float64(), mask=~ok)
        out = batch
        name = p.attribute
        out = (
            out.set_column(out.column_names.index(name), name, column)
            if name in out.column_names
            else out.append_column(name, column)
        )
        result = {"output": with_crs(out.filter(pa.array(ok)), get_crs(batch))}
        if not ok.all():
            result[REJECTED] = reject(batch.filter(pa.array(~ok)), "pas une ligne")
        return result
