"""WarpRaster : change le système de coordonnées (et au besoin la résolution) d'un raster."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import numpy as np
import pyarrow as pa
import pyproj
from pydantic import Field
from rasterio.warp import calculate_default_transform

from etl_engine.raster import core
from etl_engine.sdk import ConfigError, Crs, Modifier, Params

Resampling = Literal["nearest", "bilinear", "cubic", "cubic_spline", "lanczos", "average", "mode", "min", "max", "med"]


def warp_rasters(
    ctx: Any, batch: pa.Table, grid_for: Any, resampling: str, nodata: float | None, stem: str
) -> pa.Table:
    """Déforme chaque raster de `batch` sur la grille que donne `grid_for(descripteur)` ; renvoie la table résultat."""
    descs = core.descriptors(batch)
    attributes = core.attributes_of(batch)
    outputs = []
    for i, desc in enumerate(descs):
        ctx.check_cancel()
        grid = grid_for(desc)
        dtype = desc["dtype"]
        out_nodata = core.nodata_for(dtype, nodata if nodata is not None else desc.get("nodata"))

        def compute(window, cache, uri=desc["uri"], grid=grid, out_nodata=out_nodata, dtype=dtype):
            ds = cache.aligned(uri, grid, resampling, out_nodata, force_warp=True)
            data = ds.read(window=window, masked=True)
            return np.ma.filled(data, out_nodata if out_nodata is not None else 0).astype(dtype, copy=False)

        outputs.append(
            core.run_blocks(
                ctx,
                f"{stem}_{i}",
                grid,
                compute,
                count=desc["count"],
                dtype=dtype,
                nodata=out_nodata,
                band_names=desc["band_names"],
            )
        )
    return core.to_table(outputs, attributes)


class WarpRaster(Modifier):
    name = "WarpRaster"
    label = "Reprojection de rasters"
    category = "Rasters"
    description = "Change le système de coordonnées d'un raster, avec choix de la résolution et du rééchantillonnage."
    help = (
        "Le raster est recalculé dans le « Système cible » (EPSG:2154, EPSG:4326…). « Résolution » est la taille d'un pixel "
        "dans l'unité du système cible ; vide ou 0 = choisie pour garder à peu près le même nombre de pixels. "
        "Rééchantillonnage : nearest pour des classes (occupation du sol), bilinear ou cubic pour des valeurs continues "
        "(altitude, réflectance), average pour réduire la résolution. Le calcul se fait par fenêtres de 512 pixels, en "
        "parallèle : la mémoire reste bornée quelle que soit la taille du raster. Les pixels hors de l'image d'origine "
        "deviennent « sans donnée »."
    )
    rejected = False

    class Params(Params):
        crs: Crs = Field("", title="Système cible", description="Ex. EPSG:2154.")
        resolution: float = Field(
            0.0,
            ge=0,
            title="Résolution",
            description="Taille du pixel, dans l'unité du système cible ; 0 = automatique.",
        )
        resampling: Resampling = Field(
            "bilinear", title="Rééchantillonnage", description="Méthode de calcul des nouveaux pixels."
        )
        nodata: float | None = Field(
            None, title="Valeur sans donnée", description="Vide = celle du raster, sinon la convention du type."
        )

    def _check(self) -> None:
        if not self.params.crs:
            raise ConfigError("système de coordonnées cible non renseigné")
        try:
            pyproj.CRS.from_user_input(self.params.crs)
        except pyproj.exceptions.CRSError:
            raise ConfigError(f"système de coordonnées inconnu : {self.params.crs}") from None

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        self._check()
        core.check_raster_schema(inputs.get("input"))
        return {"output": inputs.get("input") or core.raster_schema(crs=self.params.crs)}

    def _grid(self, desc: dict[str, Any]) -> dict[str, Any]:
        p = self.params
        if not desc["crs"]:
            raise ConfigError(f"le raster {desc['uri']} n'a pas de système de coordonnées")
        left, bottom, right, top = core.extent_of(desc)
        transform, width, height = calculate_default_transform(
            desc["crs"], p.crs, desc["width"], desc["height"], left, bottom, right, top, resolution=p.resolution or None
        )
        return {
            "crs": p.crs,
            "transform": [transform.a, transform.b, transform.c, transform.d, transform.e, transform.f],
            "width": width,
            "height": height,
        }

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        self._check()
        p = self.params
        return {"output": warp_rasters(self.ctx, batch, self._grid, p.resampling, p.nodata, "reproj")}
