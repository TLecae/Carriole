"""Buffer : zone tampon autour des géométries."""

from __future__ import annotations

from typing import Literal

import numpy as np
import pyarrow as pa
import shapely
from pydantic import Field

from etl_engine.data import GEOM, geometries, get_crs, is_geographic, with_crs
from etl_engine.sdk import GEOMETRY_SLICE, REJECTED, Attribute, ConfigError, Modifier, Params, reject


class Buffer(Modifier):
    name = "Buffer"
    label = "Zone tampon"
    category = "Géométrie"
    slice_rows = GEOMETRY_SLICE
    description = "Remplace chaque géométrie par une zone tampon (buffer) de la distance indiquée."
    help = (
        "La distance est dans l'unité du système de coordonnées (des mètres en Lambert-93) ; refusé pour un "
        "système géographique (degrés). Elle peut venir d'un attribut. Une distance négative rétrécit un polygone "
        "(résultat vide = entité sur « Rejected »). Les entités sans géométrie ou à distance invalide partent "
        "sur « Rejected »."
    )

    class Params(Params):
        distance: float = Field(
            10.0,
            title="Distance",
            description=(
                "Dans l'unité du système de coordonnées (mètres en Lambert-93). Négative : rétrécit un polygone."
            ),
        )
        distance_attribute: Attribute = Field(
            "", title="Distance depuis un attribut", description="Remplace la distance fixe."
        )
        end_cap: Literal["round", "flat", "square"] = Field(
            "round",
            title="Extrémités",
            description="Forme du bout des lignes : round (arrondi), flat (plat), square (carré).",
        )
        join: Literal["round", "mitre", "bevel"] = Field(
            "round", title="Angles", description="Forme des angles : round (arrondi), mitre (angle vif), bevel (coupé)."
        )
        quad_segs: int = Field(
            16,
            ge=1,
            le=128,
            title="Segments par quart de cercle",
            description="Finesse des arrondis : segments par quart de cercle.",
        )

    def output_schema(self, inputs):
        schema = inputs.get("input")
        if schema is not None and GEOM not in schema.names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        return {"output": schema}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        p = self.params
        if GEOM not in batch.column_names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        crs = get_crs(batch)
        if is_geographic(crs):
            raise ConfigError(f"{crs} est géographique (degrés) : reprojeter d'abord (distance en mètres)")
        geoms = geometries(batch)
        if p.distance_attribute:
            if p.distance_attribute not in batch.column_names:
                raise ConfigError(f"attribut absent : {p.distance_attribute}")
            values = batch.column(p.distance_attribute).cast(pa.float64(), safe=False).to_numpy(zero_copy_only=False)
            distances = np.asarray(values, dtype=float)
        else:
            distances = np.full(len(geoms), p.distance)
        present = ~shapely.is_missing(geoms)
        good = present & np.isfinite(distances)
        buffered = np.array(geoms, dtype=object)
        if good.any():
            buffered[good] = shapely.buffer(
                geoms[good], distances[good], quad_segs=p.quad_segs, cap_style=p.end_cap, join_style=p.join
            )
        ok = good & ~shapely.is_missing(buffered) & ~shapely.is_empty(buffered)
        wkb = pa.array(shapely.to_wkb(buffered), pa.binary())
        out = batch.set_column(batch.column_names.index(GEOM), GEOM, wkb)
        result = {"output": with_crs(out.filter(pa.array(ok)), crs)}
        if not ok.all():
            reasons = np.where(~present, "pas de géométrie", np.where(~good, "distance invalide", "zone tampon vide"))
            bad = ~ok
            result[REJECTED] = reject(batch.filter(pa.array(bad)), pa.array(reasons[bad].tolist(), pa.string()))
        return result
