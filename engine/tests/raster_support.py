"""Rasters de test : de petits GeoTIFF synthétiques, sans réseau."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow as pa
import rasterio
from etl_engine.raster import core
from rasterio.transform import from_origin


def make_raster(
    path: Path,
    data: np.ndarray,
    *,
    origin: tuple[float, float] = (600000.0, 6500100.0),
    cell: float = 10.0,
    crs: str = "EPSG:2154",
    nodata: float | None = None,
    names: list[str] | None = None,
) -> Path:
    """Écrit `data` (lignes, colonnes) ou (bandes, lignes, colonnes) en GeoTIFF."""
    array = data[None] if data.ndim == 2 else data
    profile = {
        "driver": "GTiff",
        "height": array.shape[1],
        "width": array.shape[2],
        "count": array.shape[0],
        "dtype": array.dtype.name,
        "crs": crs,
        "transform": from_origin(origin[0], origin[1], cell, cell),
        "nodata": nodata,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array)
        for i, name in enumerate(names or [], 1):
            dst.set_band_description(i, name)
    return path


def raster_table(*paths: Path | str, **attributes: list) -> pa.Table:
    """Table raster (une ligne par fichier), comme le produirait RasterReader."""
    return core.to_table([core.describe(str(p)) for p in paths], attributes or None)


def read(table: pa.Table, row: int = 0) -> np.ndarray:
    """Pixels d'un raster d'une table (bandes, lignes, colonnes), masqués là où il n'y a pas de donnée."""
    uri = core.descriptors(table)[row]["uri"]
    with rasterio.open(uri) as ds:
        return ds.read(masked=True)
