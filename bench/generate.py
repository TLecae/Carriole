"""Jeux synthétiques pour le benchmark, en Lambert-93 sur la même emprise (300 × 200 km) :

- `points_N` : N points avec quelques attributs ;
- `parcelles_N` : une grille de parcelles jointives (N/10), sommets intermédiaires sur les côtés ;
- `zones_N` : des disques qui se chevauchent (N/100), recouvrement moyen 2 ;
- `mnt.tif` : un raster de 3000 × 2000 pixels de 100 m.

python bench/generate.py 1000000          # -> bench/data/points_1000000.parquet (et les autres jeux)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from etl_engine.data import GEOM, with_crs

CHUNK = 500_000
DEPS = np.array(["31", "09", "11", "12", "32", "46", "65", "81", "82", "34"])
XMIN, XMAX, YMIN, YMAX = 400_000, 700_000, 6_150_000, 6_350_000


def _write(table: pa.Table, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(with_crs(table, "EPSG:2154"), path, compression="zstd", row_group_size=100_000)
    return path


def generate_parcels(n: int, path: Path) -> Path:
    """Grille d'environ `n` parcelles jointives ; 4 sommets par côté (les côtés communs ont les mêmes sommets)."""
    cols = max(1, int(round(np.sqrt(n * 1.5))))
    rows = max(1, n // cols)
    w, h = (XMAX - XMIN) / cols, (YMAX - YMIN) / rows
    ix, iy = np.meshgrid(np.arange(cols), np.arange(rows))
    x0, y0 = XMIN + ix.ravel() * w, YMIN + iy.ravel() * h
    cells = shapely.segmentize(shapely.box(x0, y0, x0 + w, y0 + h), max(w, h) / 4)
    count = len(cells)
    return _write(
        pa.table(
            {
                "id": pa.array(np.arange(count), pa.int64()),
                "commune": pa.array([f"c{i // 100}" for i in range(count)]),
                GEOM: pa.array(shapely.to_wkb(cells), pa.binary()),
            }
        ),
        path,
    )


def generate_zones(m: int, path: Path, seed: int = 7) -> Path:
    """`m` disques aléatoires dont la surface cumulée vaut deux fois l'emprise (recouvrement moyen 2)."""
    rng = np.random.default_rng(seed)
    radius = np.sqrt(2 * (XMAX - XMIN) * (YMAX - YMIN) / (m * np.pi))
    centers = shapely.points(rng.uniform(XMIN, XMAX, m), rng.uniform(YMIN, YMAX, m))
    disks = shapely.buffer(centers, radius * rng.uniform(0.5, 1.5, m), quad_segs=8)
    return _write(
        pa.table(
            {
                "id": pa.array(np.arange(m), pa.int64()),
                "type": pa.array(DEPS[rng.integers(0, len(DEPS), m)]),
                GEOM: pa.array(shapely.to_wkb(disks), pa.binary()),
            }
        ),
        path,
    )


def generate_raster(path: Path) -> Path:
    """MNT synthétique de 3000 × 2000 pixels de 100 m (relief lisse et bruit)."""
    import rasterio
    from rasterio.transform import from_origin

    width, height = 3000, 2000
    yy, xx = np.mgrid[0:height, 0:width].astype("float32")
    data = 500 + 300 * np.sin(xx / 150) * np.cos(yy / 110) + np.random.default_rng(3).normal(0, 5, (height, width))
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "width": width,
        "height": height,
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:2154",
        "transform": from_origin(XMIN, YMAX, 100, 100),
        "tiled": True,
        "compress": "deflate",
    }
    with rasterio.open(path, "w", **profile) as ds:
        ds.write(data.astype("float32"), 1)
    return path


def generate(n: int, path: Path, seed: int = 42) -> Path:
    rng = np.random.default_rng(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = None
    for start in range(0, n, CHUNK):
        m = min(CHUNK, n - start)
        xs = rng.uniform(XMIN, XMAX, m)
        ys = rng.uniform(YMIN, YMAX, m)
        table = pa.table(
            {
                "id": pa.array(np.arange(start, start + m), pa.int64()),
                "dep": pa.array(DEPS[rng.integers(0, len(DEPS), m)]),
                "pop": pa.array(rng.integers(0, 50_000, m), pa.int64()),
                "val": pa.array(rng.normal(100, 25, m), pa.float64()),
                "nom": pa.array([f"lieu {i}" for i in range(start, start + m)]),
                GEOM: pa.array(shapely.to_wkb(shapely.points(xs, ys)), pa.binary()),
            }
        )
        table = with_crs(table, "EPSG:2154")
        if writer is None:
            writer = pq.ParquetWriter(path, table.schema, compression="zstd")
        writer.write_table(table, row_group_size=100_000)
    if writer:
        writer.close()
    return path


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
    out = generate(count, Path(__file__).parent / "data" / f"points_{count}.parquet")
    print(out)
