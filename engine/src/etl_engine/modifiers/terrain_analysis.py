"""TerrainAnalysis : pente, exposition, ombrage et rugosité d'un modèle numérique de terrain."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Literal

import numpy as np
import pyarrow as pa
from pydantic import Field
from rasterio.windows import Window

from etl_engine.data import is_geographic
from etl_engine.raster import core
from etl_engine.sdk import ConfigError, Modifier, Params

Product = Literal["slope", "aspect", "hillshade", "roughness"]


def horn(z: np.ndarray, xres: np.ndarray | float, yres: float) -> tuple[np.ndarray, np.ndarray]:
    """Dérivées dz/dx et dz/dy (méthode de Horn) sur un bloc relu avec une marge d'un pixel."""
    a, b, c = z[:-2, :-2], z[:-2, 1:-1], z[:-2, 2:]
    d, f = z[1:-1, :-2], z[1:-1, 2:]
    g, h, i = z[2:, :-2], z[2:, 1:-1], z[2:, 2:]
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * xres)
    dzdy = ((a + 2 * b + c) - (g + 2 * h + i)) / (8 * yres)  # nord moins sud : dz/dy vers le nord
    return dzdx, dzdy


class TerrainAnalysis(Modifier):
    name = "TerrainAnalysis"
    label = "Analyse de terrain (pente, exposition, ombrage)"
    category = "Rasters"
    description = "Calcule la pente, l'exposition, l'ombrage ou la rugosité d'un modèle numérique de terrain."
    help = (
        "Le raster d'entrée est un MNT (altitudes). Produits : slope (pente, en degrés ou en pourcentage), aspect (exposition "
        "en degrés depuis le nord, dans le sens horaire ; -1 sur un terrain plat), hillshade (ombrage de 0 à 255, "
        "selon « Azimut » et « Hauteur du soleil »), roughness (écart entre un pixel et ses voisins). Chaque produit "
        "devient une bande. Les dérivées utilisent la méthode de Horn (8 voisins). « Facteur Z » convertit les altitudes dans "
        "l'unité horizontale (1 si les deux sont en mètres). En degrés (WGS84), la taille des pixels est convertie en mètres "
        "à la latitude du pixel. Les pixels du bord et ceux dont un voisin est sans donnée restent sans donnée."
    )
    rejected = False

    class Params(Params):
        products: list[Product] = Field(
            default_factory=lambda: ["slope"],
            title="Produits",
            description="slope, aspect, hillshade ou roughness : une bande chacun.",
            json_schema_extra={"default": ["slope"]},
        )
        band: int = Field(1, ge=1, title="Bande", description="Bande des altitudes (à partir de 1).")
        slope_unit: Literal["degrees", "percent"] = Field(
            "degrees", title="Unité de pente", description="degrees ou percent."
        )
        z_factor: float = Field(
            1.0, gt=0, title="Facteur Z", description="Multiplie les altitudes (1 si tout est en mètres)."
        )
        azimuth: float = Field(
            315.0, ge=0, le=360, title="Azimut (ombrage)", description="Direction du soleil, en degrés depuis le nord."
        )
        altitude: float = Field(
            45.0,
            ge=0,
            le=90,
            title="Hauteur du soleil (ombrage)",
            description="Angle du soleil au-dessus de l'horizon.",
        )

    def _check(self) -> None:
        if not self.params.products:
            raise ConfigError("au moins un produit est nécessaire")

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        self._check()
        core.check_raster_schema(inputs.get("input"))
        return {"output": inputs.get("input") or core.raster_schema()}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        self._check()
        p = self.params
        descs = core.descriptors(batch)
        attributes = core.attributes_of(batch)
        outputs = []
        for i, desc in enumerate(descs):
            self.ctx.check_cancel()
            if p.band > desc["count"]:
                raise ConfigError(f"le raster n'a que {desc['count']} bande(s)")
            geographic = is_geographic(desc["crs"])
            xres, yres = core.pixel_size(desc)
            top = desc["transform"][5]
            grid = core.grid_of(desc)

            def compute(window: Window, cache, desc=desc, xres=xres, yres=yres, top=top, geographic=geographic):
                ds = cache.open(desc["uri"])
                padded = Window(window.col_off - 1, window.row_off - 1, window.width + 2, window.height + 2)
                z = ds.read(p.band, window=padded, masked=True, boundless=True, fill_value=np.nan)
                valid = ~np.ma.getmaskarray(z)
                z = np.ma.filled(z.astype("float64"), np.nan) * p.z_factor
                if geographic:  # degrés -> mètres, à la latitude de chaque ligne
                    rows = np.arange(window.row_off, window.row_off + window.height)
                    lat = np.radians(top - (rows + 0.5) * yres)
                    x_m = (xres * 111320.0 * np.cos(lat))[:, None]
                    y_m = yres * 110574.0
                else:
                    x_m, y_m = xres, yres
                dzdx, dzdy = horn(z, x_m, y_m)
                ok = valid[1:-1, 1:-1] & np.isfinite(dzdx) & np.isfinite(dzdy)
                for shift in [(0, 0), (0, 1), (0, 2), (1, 0), (1, 2), (2, 0), (2, 1), (2, 2)]:
                    ok &= valid[shift[0] : shift[0] + window.height, shift[1] : shift[1] + window.width]
                gradient = np.hypot(dzdx, dzdy)
                bands = []
                for product in p.products:
                    if product == "slope":
                        slope = np.degrees(np.arctan(gradient)) if p.slope_unit == "degrees" else gradient * 100.0
                        bands.append(slope)
                    elif product == "aspect":
                        aspect = (np.degrees(np.arctan2(-dzdx, -dzdy)) + 360.0) % 360.0  # vers où descend la pente
                        bands.append(np.where(gradient == 0, -1.0, aspect))
                    elif product == "hillshade":
                        zenith, azimuth = math.radians(90.0 - p.altitude), math.radians(360.0 - p.azimuth + 90.0)
                        slope = np.arctan(gradient)
                        aspect = np.arctan2(-dzdy, -dzdx)
                        shade = np.cos(zenith) * np.cos(slope) + np.sin(zenith) * np.sin(slope) * np.cos(
                            azimuth - aspect
                        )
                        bands.append(np.clip(255.0 * shade, 0, 255))
                    else:
                        centre = z[1:-1, 1:-1]
                        diffs = [
                            np.abs(z[1 + dy : 1 + dy + window.height, 1 + dx : 1 + dx + window.width] - centre)
                            for dy in (-1, 0, 1)
                            for dx in (-1, 0, 1)
                            if (dy, dx) != (0, 0)
                        ]
                        bands.append(np.mean(diffs, axis=0))
                stack = np.stack(bands).astype("float32")
                stack[:, ~ok] = np.nan
                return stack

            outputs.append(
                core.run_blocks(
                    self.ctx,
                    f"terrain_{i}",
                    grid,
                    compute,
                    count=len(p.products),
                    dtype="float32",
                    nodata=float("nan"),
                    band_names=list(p.products),
                )
            )
        return {"output": core.to_table(outputs, attributes)}
