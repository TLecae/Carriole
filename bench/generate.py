"""Jeux synthétiques pour le benchmark, en Lambert-93 sur la même emprise (300 × 200 km) :

- `points_N` : N points avec quelques attributs ;
- `parcelles_N` : une grille de parcelles jointives (N/10), sommets intermédiaires sur les côtés ;
- `zones_N` : des disques qui se chevauchent (N/100), recouvrement moyen 2 ;
- `mnt.tif` : un raster de 3000 × 2000 pixels de 100 m ;
- `large_N` : N lignes de 200 colonnes, surtout du texte, sans géométrie (table d'attributs volumineuse).

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


def generate_fused(m: int, path: Path, seed: int = 3) -> Path:
    """Une entité : l'union de `m` disques de 160 m sur un carré où ils se rejoignent presque tous (sortie d'un
    Dissolve de zones tampons). 300 000 disques : un multipolygone dont le plus grand morceau, d'un seul tenant, a
    1,6 million de sommets et 21 000 trous (union : ~90 s)."""
    rng = np.random.default_rng(seed)
    side = 141_000 * np.sqrt(m / 300_000)
    disks = shapely.buffer(shapely.points(rng.uniform(0, side, m), rng.uniform(0, side, m)), 160, quad_segs=4)
    fused = shapely.transform(shapely.union_all(disks), lambda xy: xy + [XMIN, YMIN])
    return _write(pa.table({"id": [1], GEOM: pa.array([shapely.to_wkb(fused)], pa.binary())}), path)


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


def generate_wide(n: int, path: Path, seed: int = 11) -> Path:
    """`n` lignes : `id`, `classe` (A à G), 120 textes à peu de valeurs, 40 presque tous différents, 38 nombres."""
    rng = np.random.default_rng(seed)
    classes = pa.array(list("ABCDEFG"))
    vocab = [pa.array([f"valeur {j} de la colonne {k}" for j in range(30)]) for k in range(120)]
    free = pa.array([f"texte libre numéro {j:06d}" for j in range(100_000)])
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = None
    for start in range(0, n, CHUNK // 5):
        m = min(CHUNK // 5, n - start)

        def pick(values: pa.Array, m: int = m) -> pa.Array:
            return values.take(pa.array(rng.integers(0, len(values), m)))

        columns = {"id": pa.array(np.arange(start, start + m), pa.int64()), "classe": pick(classes)}
        columns |= {f"cat_{k:03d}": pick(vocab[k]) for k in range(120)}
        columns |= {f"txt_{k:03d}": pick(free) for k in range(40)}
        columns |= {f"num_{k:03d}": pa.array(rng.normal(100, 25, m)) for k in range(38)}
        table = pa.table(columns)
        if writer is None:
            writer = pq.ParquetWriter(path, table.schema, compression="zstd")
        writer.write_table(table, row_group_size=100_000)
    if writer:
        writer.close()
    return path


DRIVERS = {"gpkg": "GPKG", "shp": "ESRI Shapefile", "geojson": "GeoJSON", "fgb": "FlatGeobuf"}


def convert_points(src: Path, fmt: str, path: Path) -> Path:
    """Les points de `src` dans un autre format (cas « lecture_<format> » du banc) ; CSV : géométrie en WKT."""
    import duckdb
    import pyogrio.raw
    from etl_engine import duck

    if fmt == "csv":
        con = duckdb.connect()
        duck.load_spatial(con)
        con.execute(
            f"COPY (SELECT * EXCLUDE (geometry), ST_AsText(ST_GeomFromWKB(geometry)) AS wkt "
            f"FROM read_parquet('{src.as_posix()}')) TO '{path.as_posix()}' (HEADER)"
        )
        return path
    pyogrio.raw.write_arrow(
        pq.read_table(src), str(path), driver=DRIVERS[fmt], geometry_name=GEOM, geometry_type="Point",
        crs="EPSG:2154", layer="points",
    )  # fmt: skip
    return path


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
    out = generate(count, Path(__file__).parent / "data" / f"points_{count}.parquet")
    print(out)
