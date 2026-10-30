"""Inspecteur de rasters : description, statistiques d'affichage, tuiles PNG et valeur d'un pixel.

Les pixels ne passent jamais par le cache Arrow : une ligne d'un port raster désigne un fichier (voir l'ADR 0026). Les
tuiles sont calculées à la demande en Web Mercator par déformation à la volée (VRT), donc sans copie du raster.
"""

from __future__ import annotations

import struct
import threading
import zlib
from collections import OrderedDict
from typing import Any

import numpy as np
import pyarrow.parquet as pq
import rasterio
from etl_engine.raster import core
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT

from etl_api.data_service import DataError, Port

WORLD = 20037508.342789244
TILE = 256
SAMPLE_PIXELS = 1_000_000

# Palettes : (position 0-1, rouge, vert, bleu) — dégradés lisibles, sans dépendance.
PALETTES: dict[str, list[tuple[float, int, int, int]]] = {
    "gray": [(0.0, 0, 0, 0), (1.0, 255, 255, 255)],
    "viridis": [(0.0, 68, 1, 84), (0.25, 59, 82, 139), (0.5, 33, 145, 140), (0.75, 94, 201, 98), (1.0, 253, 231, 37)],
    "terrain": [
        (0.0, 51, 102, 153),
        (0.15, 60, 140, 60),
        (0.45, 200, 190, 100),
        (0.75, 140, 100, 70),
        (1.0, 250, 250, 250),
    ],
    "ndvi": [(0.0, 140, 80, 40), (0.35, 235, 220, 150), (0.6, 130, 190, 80), (1.0, 20, 100, 30)],
    "spectral": [
        (0.0, 158, 1, 66),
        (0.25, 244, 109, 67),
        (0.5, 255, 255, 191),
        (0.75, 102, 194, 165),
        (1.0, 94, 79, 162),
    ],
}

_lock = threading.Lock()
_stats: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()


def descriptor(port: Port, fid: int) -> dict[str, Any]:
    """La ligne `fid` (rang dans le fichier du port) de la colonne `raster`."""
    try:
        table = pq.read_table(port.data, columns=[core.RASTER])
    except (KeyError, ValueError, OSError):
        raise DataError("ce port ne contient pas de raster", 404) from None
    if not 0 <= fid < table.num_rows:
        raise DataError("raster introuvable", 404)
    return table.column(core.RASTER)[fid].as_py()


def _open(desc: dict[str, Any]) -> rasterio.DatasetReader:
    try:
        return rasterio.open(core.gdal_uri(desc["uri"]))
    except Exception as exc:  # noqa: BLE001 - fichier déplacé, adresse injoignable…
        raise DataError(f"raster illisible : {exc}", 404) from exc


def bands_of(desc: dict[str, Any], requested: str | None) -> list[int]:
    """Bandes à afficher (à partir de 1) : celles demandées, sinon RVB s'il y en a au moins 3 (nommées red/green/blue
    sinon les trois premières), sinon la première."""
    if requested:
        try:
            wanted = [int(b) for b in requested.split(",") if b.strip()]
        except ValueError:
            raise DataError("bandes invalides", 400) from None
        if not wanted or len(wanted) not in (1, 3) or any(not 1 <= b <= desc["count"] for b in wanted):
            raise DataError("choisir une bande ou trois bandes existantes", 400)
        return wanted
    return [1, 2, 3] if desc["count"] >= 3 and desc["dtype"] in ("uint8", "uint16") else [1]


def display_range(desc: dict[str, Any], band: int) -> tuple[float, float]:
    """Étendue d'affichage d'une bande : percentiles 2 et 98 d'une lecture réduite (gardée en mémoire)."""
    key = (desc["uri"], band)
    with _lock:
        if key in _stats:
            _stats.move_to_end(key)
            return _stats[key]["range"]
    with core.gdal_env(), _open(desc) as ds:
        scale = max(1.0, (ds.width * ds.height / SAMPLE_PIXELS) ** 0.5)
        shape = (max(1, int(ds.height / scale)), max(1, int(ds.width / scale)))
        data = ds.read(band, out_shape=shape, masked=True, resampling=Resampling.nearest).compressed().astype("float64")
    data = data[np.isfinite(data)]
    low, high = (float(np.percentile(data, 2)), float(np.percentile(data, 98))) if data.size else (0.0, 1.0)
    if high <= low:
        high = low + 1.0
    with _lock:
        _stats[key] = {"range": (low, high)}
        while len(_stats) > 256:
            _stats.popitem(last=False)
    return low, high


def info(port: Port, fid: int) -> dict[str, Any]:
    desc = descriptor(port, fid)
    default = bands_of(desc, None)
    return {
        **desc,
        "bands": [
            {"index": i, "name": desc["band_names"][i - 1] if i <= len(desc["band_names"]) else f"b{i}"}
            for i in range(1, desc["count"] + 1)
        ],
        "display": {"bands": default, "ranges": {str(b): display_range(desc, b) for b in default}},
        "palettes": list(PALETTES),
    }


def png(rgba: np.ndarray) -> bytes:
    """PNG RGBA 8 bits à partir d'un tableau (hauteur, largeur, 4)."""
    height, width, _ = rgba.shape
    raw = b"".join(b"\x00" + rgba[row].tobytes() for row in range(height))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 3)) + chunk(b"IEND", b"")


EMPTY_TILE = png(np.zeros((1, 1, 4), dtype=np.uint8))


def tile(
    port: Port,
    fid: int,
    z: int,
    x: int,
    y: int,
    bands: str | None = None,
    vmin: float | None = None,
    vmax: float | None = None,
    palette: str = "viridis",
    resampling: str = "bilinear",
) -> bytes:
    if not (0 <= z <= 24 and 0 <= x < 2**z and 0 <= y < 2**z):
        raise DataError("tuile hors pyramide", 404)
    desc = descriptor(port, fid)
    if not desc["crs"]:
        raise DataError("le raster n'a pas de système de coordonnées", 404)
    chosen = bands_of(desc, bands)
    resolution = 2 * WORLD / 2**z
    left, top = -WORLD + x * resolution, WORLD - y * resolution
    transform = from_bounds(left, top - resolution, left + resolution, top, TILE, TILE)
    if resampling not in ("nearest", "bilinear", "cubic", "average"):
        raise DataError("rééchantillonnage invalide", 400)
    with core.gdal_env(), _open(desc) as ds:
        # hors de l'emprise du raster : tuile vide, sans ouvrir de déformation
        from pyproj import Transformer

        box = Transformer.from_crs("EPSG:3857", ds.crs, always_xy=True).transform_bounds(
            left, top - resolution, left + resolution, top
        )
        if box[0] > ds.bounds.right or box[2] < ds.bounds.left or box[1] > ds.bounds.top or box[3] < ds.bounds.bottom:
            return EMPTY_TILE
        options: dict[str, Any] = {"src_nodata": ds.nodata} if ds.nodata is not None else {}
        with WarpedVRT(
            ds,
            crs="EPSG:3857",
            transform=transform,
            width=TILE,
            height=TILE,
            resampling=Resampling[resampling],
            add_alpha=True,
            **options,
        ) as vrt:
            data = vrt.read(chosen, masked=False).astype("float64")
            alpha = (
                vrt.read(vrt.count, masked=False) > 0 if vrt.count > len(chosen) else np.ones((TILE, TILE), dtype=bool)
            )
    valid = alpha.copy()
    for plane in data:
        valid &= np.isfinite(plane)
    rgba = np.zeros((TILE, TILE, 4), dtype=np.uint8)
    if len(chosen) == 3:
        for k, band in enumerate(chosen):
            low, high = (vmin, vmax) if vmin is not None and vmax is not None else display_range(desc, band)
            rgba[..., k] = np.clip((data[k] - low) / (high - low) * 255.0, 0, 255).astype(np.uint8)
    else:
        low, high = (vmin, vmax) if vmin is not None and vmax is not None else display_range(desc, chosen[0])
        if high <= low:
            high = low + 1.0
        stops = PALETTES.get(palette)
        if stops is None:
            raise DataError("palette inconnue", 400)
        position = np.clip((data[0] - low) / (high - low), 0.0, 1.0)
        xs = [s[0] for s in stops]
        for k in range(3):
            rgba[..., k] = np.interp(position, xs, [s[k + 1] for s in stops]).astype(np.uint8)
    rgba[..., 3] = np.where(valid, 255, 0).astype(np.uint8)
    return png(rgba)


def pixel(port: Port, fid: int, lon: float, lat: float) -> dict[str, Any]:
    """Valeur de chaque bande au point (longitude, latitude WGS84)."""
    from pyproj import Transformer

    desc = descriptor(port, fid)
    if not desc["crs"]:
        raise DataError("le raster n'a pas de système de coordonnées", 404)
    x, y = Transformer.from_crs("EPSG:4326", desc["crs"], always_xy=True).transform(lon, lat)
    with core.gdal_env(), _open(desc) as ds:
        row, col = ds.index(x, y)
        if not (0 <= row < ds.height and 0 <= col < ds.width):
            return {"inside": False, "values": {}}
        window = rasterio.windows.Window(col, row, 1, 1)
        data = ds.read(window=window, masked=True)
    names = desc["band_names"]
    values = {
        (names[i] if i < len(names) and names[i] else f"b{i + 1}"): (
            None if np.ma.is_masked(data[i, 0, 0]) else float(data[i, 0, 0])
        )
        for i in range(data.shape[0])
    }
    return {"inside": True, "row": int(row), "col": int(col), "x": x, "y": y, "values": values}
