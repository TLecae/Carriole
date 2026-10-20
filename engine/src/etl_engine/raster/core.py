"""Rasters dans le flux : modèle de données et outils communs (lecture par fenêtres, écriture, environnement GDAL).

**Un port raster est une table Arrow dont chaque ligne est un raster** (ADR 0026). Colonnes :
- `raster` : structure (uri, driver, width, height, count, dtype, nodata, transform, crs, band_names) ;
- `geometry` : l'emprise du raster (WKB), dans le système du premier raster de la table : l'inspecteur la montre sur la
  carte, et ExtractByExpression, OrderBy, les jointures… travaillent dessus comme sur n'importe quelle entité ;
- les autres colonnes sont des attributs (date, couverture nuageuse d'une scène STAC…).

Les pixels ne passent jamais par Arrow : une sortie raster est un fichier (GeoTIFF tuilé) écrit dans le dossier du nœud
(`ctx.work_dir`), une source est référencée sur place (fichier local, partage, ou `/vsicurl/` pour du HTTP).
"""

from __future__ import annotations

import contextlib
import math
import os
import tempfile
import threading
import uuid
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyproj
import rasterio
import shapely
from rasterio.windows import Window

from etl_engine.data import GEOM, get_crs, with_crs
from etl_engine.sdk import ConfigError

RASTER = "raster"
BLOCK = 512  # côté des fenêtres de traitement et des tuiles écrites

STRUCT = pa.struct(
    [
        ("uri", pa.string()),
        ("driver", pa.string()),
        ("width", pa.int32()),
        ("height", pa.int32()),
        ("count", pa.int32()),
        ("dtype", pa.string()),
        ("nodata", pa.float64()),
        ("transform", pa.list_(pa.float64())),
        ("crs", pa.string()),
        ("band_names", pa.list_(pa.string())),
    ]
)

# Options GDAL pour le HTTP (STAC, COG distants) : peu d'allers-retours, lecture par plages, cache de blocs.
GDAL_OPTIONS: dict[str, Any] = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff,.vrt,.jp2,.nc,.asc",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "VSI_CACHE": "TRUE",
    "VSI_CACHE_SIZE": 64 * 1024 * 1024,
    "GDAL_NUM_THREADS": "ALL_CPUS",
    "GDAL_CACHEMAX": 512,
}


def gdal_env(**extra: Any) -> rasterio.Env:
    """Environnement GDAL des opérations raster (proxy du poste compris, s'il est configuré)."""
    options = dict(GDAL_OPTIONS)
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or os.environ.get("HTTP_PROXY")
    if proxy:
        options["GDAL_HTTP_PROXY"] = proxy
    options.update(extra)
    return rasterio.Env(**options)


def is_remote(uri: str) -> bool:
    return uri.lower().startswith(("http://", "https://", "/vsicurl/", "/vsis3/"))


def gdal_uri(uri: str) -> str:
    """Chemin GDAL d'une source : une adresse web devient `/vsicurl/…`."""
    if uri.lower().startswith(("http://", "https://")):
        return "/vsicurl/" + uri
    return uri


# ------------------------------------------------------------------ descripteurs et tables
def describe(uri: str, band_names: Sequence[str] | None = None) -> dict[str, Any]:
    """Métadonnées d'un raster (sans lire de pixels)."""
    with gdal_env(), rasterio.open(gdal_uri(uri)) as ds:
        names = list(band_names) if band_names else [d or f"b{i}" for i, d in enumerate(ds.descriptions, 1)]
        t = ds.transform
        return {
            "uri": uri,
            "driver": ds.driver,
            "width": ds.width,
            "height": ds.height,
            "count": ds.count,
            "dtype": ds.dtypes[0] if ds.count else "float32",
            "nodata": float(ds.nodata) if ds.nodata is not None else None,
            "transform": [t.a, t.b, t.c, t.d, t.e, t.f],
            "crs": ds.crs.to_string() if ds.crs else None,
            "band_names": names,
        }


def extent_of(desc: dict[str, Any]) -> tuple[float, float, float, float]:
    """(xmin, ymin, xmax, ymax) d'un raster, dans son système."""
    a, b, c, d, e, f = desc["transform"]
    xs = [c, c + a * desc["width"], c + b * desc["height"], c + a * desc["width"] + b * desc["height"]]
    ys = [f, f + d * desc["width"], f + e * desc["height"], f + d * desc["width"] + e * desc["height"]]
    return min(xs), min(ys), max(xs), max(ys)


def to_table(descs: Sequence[dict[str, Any]], attributes: dict[str, Sequence[Any]] | None = None) -> pa.Table:
    """Table raster à partir de descripteurs : colonne `raster`, emprise `geometry`, attributs facultatifs."""
    crs = next((d["crs"] for d in descs if d.get("crs")), None)
    boxes = []
    for d in descs:
        xmin, ymin, xmax, ymax = extent_of(d)
        if crs and d.get("crs") and d["crs"] != crs:  # emprise ramenée au système du premier raster
            xmin, ymin, xmax, ymax = pyproj.Transformer.from_crs(d["crs"], crs, always_xy=True).transform_bounds(
                xmin, ymin, xmax, ymax
            )
        boxes.append(shapely.box(xmin, ymin, xmax, ymax))
    columns: dict[str, pa.Array] = {RASTER: pa.array([dict(d) for d in descs], STRUCT)}
    for name, values in (attributes or {}).items():
        columns[name] = pa.array(list(values))
    columns[GEOM] = pa.array(shapely.to_wkb(np.array(boxes, dtype=object)), pa.binary())
    return with_crs(pa.table(columns), crs)


def descriptors(table: pa.Table) -> list[dict[str, Any]]:
    """Les rasters d'une table, un dictionnaire par ligne."""
    if RASTER not in table.column_names:
        raise ConfigError("l'entrée n'est pas un raster : elle n'a pas de colonne « raster »")
    return table.column(RASTER).to_pylist()


def attributes_of(table: pa.Table) -> dict[str, list[Any]]:
    """Les colonnes autres que `raster` et `geometry` (attributs des rasters)."""
    return {n: table.column(n).to_pylist() for n in table.column_names if n not in (RASTER, GEOM)}


def raster_schema(extra: Sequence[pa.Field] = (), crs: str | None = None) -> pa.Schema:
    """Schéma d'un port raster (pour `output_schema`)."""
    return with_crs(
        pa.schema([pa.field(RASTER, STRUCT), *extra, pa.field(GEOM, pa.binary())]).empty_table(), crs
    ).schema


def check_raster_schema(schema: pa.Schema | None) -> None:
    if schema is not None and RASTER not in schema.names:
        raise ConfigError("l'entrée n'est pas un raster : elle n'a pas de colonne « raster »")


# ------------------------------------------------------------------ fichiers de sortie
def output_path(ctx: Any, stem: str, suffix: str = ".tif") -> Path:
    """Chemin d'un raster produit par un nœud : dans son dossier de travail, sans collision."""
    folder = getattr(ctx, "work_dir", None)
    folder = Path(folder) if folder else Path(tempfile.mkdtemp(prefix="etl-raster-"))
    folder.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in stem)[:80] or "raster"
    return folder / f"{safe}_{uuid.uuid4().hex[:8]}{suffix}"


def profile_like(desc: dict[str, Any], **override: Any) -> dict[str, Any]:
    """Profil de création GeoTIFF tuilé et compressé, à partir d'un descripteur."""
    a, b, c, d, e, f = desc["transform"]
    profile: dict[str, Any] = {
        "driver": "GTiff",
        "width": desc["width"],
        "height": desc["height"],
        "count": desc["count"],
        "dtype": desc["dtype"],
        "crs": desc["crs"],
        "transform": rasterio.Affine(a, b, c, d, e, f),
        "nodata": desc["nodata"],
        "tiled": True,
        "blockxsize": BLOCK,
        "blockysize": BLOCK,
        "compress": "DEFLATE",
        "predictor": 2 if np.issubdtype(np.dtype(desc["dtype"]), np.integer) else 3,
        "BIGTIFF": "IF_SAFER",
        "NUM_THREADS": "ALL_CPUS",
    }
    profile.update(override)
    if profile["width"] < BLOCK or profile["height"] < BLOCK:
        profile.pop("tiled", None)
        profile.pop("blockxsize", None)
        profile.pop("blockysize", None)
    return profile


def descriptor_of_file(path: str | Path, band_names: Sequence[str] | None = None) -> dict[str, Any]:
    return describe(str(path), band_names)


# ------------------------------------------------------------------ traitement par fenêtres
def windows(width: int, height: int, size: int = BLOCK) -> list[Window]:
    """Fenêtres alignées sur les blocs, dans l'ordre des lignes."""
    return [
        Window(col, row, min(size, width - col), min(size, height - row))
        for row in range(0, height, size)
        for col in range(0, width, size)
    ]


class DatasetCache:
    """Les handles de lecture d'un thread : rasterio ne partage pas un dataset entre threads."""

    def __init__(self) -> None:
        self.handles: dict[Any, Any] = {}

    def open(self, uri: str) -> rasterio.DatasetReader:
        ds = self.handles.get(uri)
        if ds is None:
            ds = self.handles[uri] = rasterio.open(gdal_uri(uri))
        return ds

    def aligned(
        self,
        uri: str,
        grid: dict[str, Any],
        resampling: str = "nearest",
        nodata: float | None = None,
        force_warp: bool = False,
    ) -> Any:
        """La source `uri` vue sur la grille de `grid` (même système, même pas, même emprise) : lue telle quelle si elle
        la partage déjà, sinon rééchantillonnée à la volée (VRT déformé)."""
        key = (
            uri,
            grid["crs"],
            tuple(grid["transform"]),
            grid["width"],
            grid["height"],
            resampling,
            nodata,
            force_warp,
        )
        ds = self.handles.get(key)
        if ds is None:
            source = self.open(uri)
            if same_grid_as(source, grid) and not force_warp:
                ds = source
            else:
                from rasterio.enums import Resampling
                from rasterio.vrt import WarpedVRT

                options: dict[str, Any] = {}
                if nodata is not None and nodata == nodata:
                    options["nodata"] = nodata
                elif nodata is not None:  # NaN
                    options["nodata"] = float("nan")
                if source.nodata is not None:
                    options["src_nodata"] = source.nodata
                ds = WarpedVRT(
                    source,
                    crs=grid["crs"],
                    transform=rasterio.Affine(*grid["transform"]),
                    width=grid["width"],
                    height=grid["height"],
                    resampling=Resampling[resampling],
                    **options,
                )
            self.handles[key] = ds
        return ds

    def close(self) -> None:
        for ds in list(self.handles.values()):
            with contextlib.suppress(Exception):  # fermeture au mieux
                ds.close()
        self.handles.clear()


def same_grid_as(ds: Any, grid: dict[str, Any]) -> bool:
    t = ds.transform
    return (
        ds.width == grid["width"]
        and ds.height == grid["height"]
        and (ds.crs.to_string() if ds.crs else None) == grid["crs"]
        and all(
            math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-9)
            for x, y in zip((t.a, t.b, t.c, t.d, t.e, t.f), grid["transform"], strict=True)
        )
    )


def worker_count() -> int:
    configured = os.environ.get("ETL_RASTER_THREADS")
    if configured:
        return max(1, int(configured))
    return max(1, min(8, (os.cpu_count() or 2) // 2 + 1))


def map_windows(
    shape: tuple[int, int],
    compute: Callable[[Window, DatasetCache], np.ndarray | None],
    write: Callable[[Window, np.ndarray], None],
    *,
    block: int = BLOCK,
    threads: int | None = None,
    check_cancel: Callable[[], None] | None = None,
) -> None:
    """Calcule chaque fenêtre dans un thread (rasterio relâche le GIL) et écrit les résultats dans le thread appelant.

    La mémoire est bornée : au plus `2 × threads` fenêtres sont en vol. `compute` reçoit les handles propres au thread ;
    `write` n'est appelé que depuis le thread appelant.
    """
    threads = threads or worker_count()
    local = threading.local()
    caches: list[DatasetCache] = []
    lock = threading.Lock()

    def start() -> None:
        local.cache = DatasetCache()
        with lock:
            caches.append(local.cache)

    def job(window: Window) -> tuple[Window, np.ndarray | None]:
        return window, compute(window, local.cache)

    todo = iter(windows(shape[1], shape[0], block))
    with (
        gdal_env(),
        ThreadPoolExecutor(max_workers=threads, thread_name_prefix="etl-raster", initializer=start) as pool,
    ):
        pending: set = set()
        try:
            while True:
                while len(pending) < threads * 2:
                    window = next(todo, None)
                    if window is None:
                        break
                    pending.add(pool.submit(job, window))
                if not pending:
                    break
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    window, data = future.result()
                    if data is not None:
                        write(window, data)
                if check_cancel:
                    check_cancel()
        except BaseException:
            for future in pending:
                future.cancel()
            raise
        finally:
            pool.shutdown(wait=True)
            for cache in caches:
                cache.close()


@contextmanager
def writer(
    path: Path, profile: dict[str, Any], band_names: Sequence[str] | None = None
) -> Iterator[rasterio.DatasetWriter]:
    """Ouvre un GeoTIFF en écriture ; en cas d'erreur le fichier incomplet est supprimé."""
    with gdal_env():
        try:
            with rasterio.open(path, "w", **profile) as dst:
                if band_names:
                    for i, name in enumerate(band_names[: profile["count"]], 1):
                        dst.set_band_description(i, name)
                yield dst
        except BaseException:
            Path(path).unlink(missing_ok=True)
            raise


def is_geographic(crs: str | None) -> bool:
    if not crs:
        return False
    try:
        return bool(pyproj.CRS.from_user_input(crs).is_geographic)
    except pyproj.exceptions.CRSError:
        return False


def pixel_size(desc: dict[str, Any]) -> tuple[float, float]:
    a, b, _, d, e, _ = desc["transform"]
    return math.hypot(a, d), math.hypot(b, e)


def table_crs(table: pa.Table | pa.Schema) -> str | None:
    return get_crs(table)


# ------------------------------------------------------------------ une entrée raster, une sortie raster
DEFAULT_NODATA = {
    "uint8": 255,
    "uint16": 65535,
    "uint32": 4294967295,
    "int8": -128,
    "int16": -32768,
    "int32": -2147483648,
    "float32": float("nan"),
    "float64": float("nan"),
}


def nodata_for(dtype: str, wanted: float | None = None) -> float | None:
    """La valeur « sans donnée » d'une sortie : celle demandée, sinon la convention du type."""
    return wanted if wanted is not None else DEFAULT_NODATA.get(dtype)


def read_window(
    ds: Any, window: Window, indexes: Sequence[int] | None = None, fill: float = np.nan
) -> tuple[np.ndarray, np.ndarray]:
    """(valeurs en float32/64, masque des pixels valides) d'une fenêtre ; les pixels sans donnée valent `fill`."""
    data = ds.read(list(indexes) if indexes else None, window=window, masked=True)
    valid = ~np.ma.getmaskarray(data)
    dtype = np.float64 if data.dtype == np.float64 else np.float32
    values = np.ma.filled(data.astype(dtype), fill)
    return values, valid


def run_blocks(
    ctx: Any,
    stem: str,
    grid: dict[str, Any],
    compute: Callable[[Window, DatasetCache], np.ndarray | None],
    *,
    count: int,
    dtype: str,
    nodata: float | None,
    band_names: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Écrit un nouveau GeoTIFF sur la grille `grid` (crs, transform, width, height) : `compute(fenêtre, handles)`
    rend un tableau (bandes, lignes, colonnes) du type `dtype`. Renvoie le descripteur du fichier produit."""
    path = output_path(ctx, stem)
    desc = {**grid, "count": count, "dtype": dtype, "nodata": nodata}
    profile = profile_like(desc)
    cancel = getattr(ctx, "check_cancel", None)
    with writer(path, profile, band_names) as dst:
        map_windows(
            (grid["height"], grid["width"]),
            compute,
            lambda window, array: dst.write(array.astype(dtype, copy=False), window=window),
            check_cancel=cancel,
        )
    return describe(str(path), band_names)


def grid_of(desc: dict[str, Any]) -> dict[str, Any]:
    return {k: desc[k] for k in ("crs", "transform", "width", "height")}


def valid_to_nodata(values: np.ndarray, valid: np.ndarray, nodata: float | None, dtype: str) -> np.ndarray:
    """Remplace les pixels invalides par la valeur sans donnée de la sortie."""
    out = np.where(valid, values, nodata if nodata is not None else 0)
    return out.astype(dtype, copy=False)


def pixel_window(transform: Any, bounds: Sequence[float], width: int, height: int) -> Window | None:
    """La fenêtre de pixels entiers qui couvre `bounds` (xmin, ymin, xmax, ymax), bornée au raster ; None si vide."""
    from rasterio.windows import from_bounds

    w = from_bounds(*bounds, transform=transform)
    col0, row0 = max(0, math.floor(w.col_off + 1e-9)), max(0, math.floor(w.row_off + 1e-9))
    col1, row1 = min(width, math.ceil(w.col_off + w.width - 1e-9)), min(height, math.ceil(w.row_off + w.height - 1e-9))
    if col1 <= col0 or row1 <= row0:
        return None
    return Window(col0, row0, col1 - col0, row1 - row0)
