"""Contour : courbes de niveau d'un modèle numérique de terrain."""

from __future__ import annotations

import math
from collections.abc import Mapping

import contourpy
import numpy as np
import pyarrow as pa
import rasterio
import shapely
from pydantic import Field

from etl_engine.data import GEOM, set_geometries
from etl_engine.raster import core
from etl_engine.sdk import ConfigError, Modifier, Params

MAX_PIXELS = 40_000_000


class Contour(Modifier):
    name = "Contour"
    label = "Courbes de niveau"
    category = "Rasters"
    description = (
        "Trace les courbes de niveau d'un modèle numérique de terrain : une ligne par courbe, avec son altitude."
    )
    help = (
        "Une courbe est tracée tous les « Équidistance » (10 m : 100, 110, 120…), à partir de « Altitude de base », ou "
        "pour les altitudes de « Niveaux » si la liste est renseignée. Chaque ligne porte son altitude (« Attribut de "
        "l'altitude ») et les attributs du raster. « Longueur minimale » écarte les petites courbes (îlots). Les pixels "
        "sans donnée interrompent les courbes. Le raster est lu en mémoire (au plus 40 millions de pixels : découper "
        "d'abord au-delà). Les lignes sont dans le système du raster."
    )
    rejected = False

    class Params(Params):
        band: int = Field(1, ge=1, title="Bande", description="Bande des altitudes (à partir de 1).")
        interval: float = Field(10.0, gt=0, title="Équidistance", description="Écart d'altitude entre deux courbes.")
        base: float = Field(
            0.0, title="Altitude de base", description="Altitude à laquelle la série de courbes est ancrée."
        )
        levels: list[float] = Field(
            default_factory=list, title="Niveaux", description="Altitudes précises (remplacent l'équidistance)."
        )
        min_length: float = Field(
            0.0, ge=0, title="Longueur minimale", description="Écarte les courbes plus courtes (unité du système)."
        )
        elevation_attribute: str = Field(
            "elevation", title="Attribut de l'altitude", description="Reçoit l'altitude de la courbe."
        )

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        schema = inputs.get("input")
        core.check_raster_schema(schema)
        others = [f for f in (schema or []) if f.name not in ("raster", GEOM)]
        return {
            "output": pa.schema(
                [*others, pa.field(self.params.elevation_attribute, pa.float64()), pa.field(GEOM, pa.binary())]
            )
        }

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        p = self.params
        descs = core.descriptors(batch)
        attributes = core.attributes_of(batch)
        pieces: list[pa.Table] = []
        for r, desc in enumerate(descs):
            self.ctx.check_cancel()
            if desc["width"] * desc["height"] > MAX_PIXELS:
                raise ConfigError(
                    f"raster trop grand pour les courbes de niveau ({desc['width']} x {desc['height']}) : le découper d'abord"
                )
            if p.band > desc["count"]:
                raise ConfigError(f"le raster n'a que {desc['count']} bande(s)")
            a, b, c, d, e, f = desc["transform"]
            if b or d:
                raise ConfigError("les rasters pivotés ne sont pas gérés")
            with core.gdal_env(), rasterio.open(core.gdal_uri(desc["uri"])) as ds:
                z = ds.read(p.band, masked=True)
            if z.mask.all():
                continue
            x = c + a * (np.arange(desc["width"]) + 0.5)
            y = f + e * (np.arange(desc["height"]) + 0.5)
            order = slice(None, None, -1) if e < 0 else slice(None)  # contourpy veut des ordonnées croissantes
            generator = contourpy.contour_generator(
                x, y[order], np.ma.masked_invalid(z[order].astype("float64")), line_type=contourpy.LineType.Separate
            )
            low, high = float(z.min()), float(z.max())
            levels = (
                sorted(p.levels)
                if p.levels
                else [
                    p.base + k * p.interval
                    for k in range(math.ceil((low - p.base) / p.interval), math.floor((high - p.base) / p.interval) + 1)
                ]
            )
            lines, elevations = [], []
            for level in levels:
                for points in generator.lines(level):
                    if len(points) >= 2:
                        line = shapely.LineString(points)
                        if line.length >= p.min_length:
                            lines.append(line)
                            elevations.append(float(level))
            if not lines:
                continue
            n = len(lines)
            table = pa.table({"_row": pa.array(range(n), pa.int64())})
            for k, v in attributes.items():
                table = table.append_column(k, pa.repeat(v[r], n))
            table = table.drop_columns(["_row"])
            table = table.append_column(p.elevation_attribute, pa.array(elevations, pa.float64()))
            table = table.append_column(GEOM, pa.nulls(n, pa.binary()))
            pieces.append(set_geometries(table, np.array(lines, dtype=object), desc["crs"]))
        if not pieces:
            return {}
        self.ctx.info(f"{sum(t.num_rows for t in pieces)} courbe(s)")
        return {"output": pa.concat_tables(pieces, promote_options="default")}
