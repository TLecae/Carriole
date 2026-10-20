"""Flux de rasters de bout en bout : les deux cas d'usage du jalon 14, sans réseau."""

import threading
import time

import numpy as np
import psutil
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import rasterio
import shapely
from etl_engine.data import GEOM, with_crs
from etl_engine.executor import run_flow
from raster_support import make_raster
from test_raster_core import Catalog, scene, stac  # noqa: F401 - le catalogue STAC simulé et la scène de test

from conftest import make_flow


def test_sentinel_style_flow_stac_ndvi_zonal_statistics_per_commune(stac, scene, registry, tmp_path):  # noqa: F811
    path, red, nir = scene
    zones = with_crs(
        pa.table(
            {
                "commune": ["Nord", "Sud"],
                GEOM: pa.array(
                    [
                        shapely.to_wkb(shapely.box(600000, 6499600, 601000, 6500100)),
                        shapely.to_wkb(shapely.box(600000, 6499100, 601000, 6499600)),
                    ],
                    pa.binary(),
                ),
            }
        ),
        "EPSG:2154",
    )
    pq.write_table(zones, tmp_path / "communes.parquet")
    flow = make_flow(
        [
            {"id": "z", "type": "Reader", "params": {"path": str(tmp_path / "communes.parquet")}},
            {
                "id": "s",
                "type": "STACSearch",
                "params": {"api_url": stac, "collections": ["demo"], "assets": ["red", "nir"], "bbox": [1, 43, 2, 44]},
            },
            {
                "id": "c",
                "type": "RasterCalculator",
                "params": {"calculations": [{"name": "ndvi", "expression": "(nir - red) / (nir + red)"}]},
            },
            {
                "id": "q",
                "type": "ZonalStatistics",
                "params": {"stats": ["mean", "count"], "raster_attributes": ["stac_id", "cloud_cover"]},
            },
            {"id": "w", "type": "Writer", "params": {"path": str(tmp_path / "ndvi_par_commune.csv")}},
        ],
        [
            ("s", "output", "c", "input"),
            ("z", "output", "q", "zones"),
            ("c", "output", "q", "raster"),
            ("q", "output", "w", "input"),
        ],
    )
    result = run_flow(flow, registry, tmp_path / "run")
    assert result.status == "done", result.error
    import csv

    with open(tmp_path / "ndvi_par_commune.csv", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [(r["commune"], r["stac_id"], r["cloud_cover"]) for r in rows] == [
        ("Nord", "scene-1", "10"),
        ("Sud", "scene-1", "10"),
        ("Nord", "scene-2", "20"),
        ("Sud", "scene-2", "20"),
    ]
    ndvi = (nir.astype("float64") - red) / (nir + red + 1e-12)
    valid = (nir > 0) | (red > 0)
    north = ndvi[:50][valid[:50]]  # la moitié nord de la scène (lignes 0 à 49)
    got = float(rows[0]["ndvi_mean"])
    assert abs(got - float(north.mean())) < 1e-3 and int(float(rows[0]["ndvi_count"])) == north.size


def test_dem_flow_tiles_mosaic_slope(registry, tmp_path):
    """Des dalles ASC → mosaïque → pente → fichier COG : le flux MNT du jalon."""
    y, x = np.mgrid[0:40, 0:80]
    z = (x * 2.0).astype("float32")  # plan incliné vers l'est : pente atan(2/10)
    for name, cols in (("dalle_1", slice(0, 40)), ("dalle_2", slice(40, 80))):
        make_raster(
            tmp_path / f"{name}.tif", z[:, cols], origin=(600000.0 + cols.start * 10, 6500000.0), nodata=-9999.0
        )
        with (
            rasterio.open(tmp_path / f"{name}.tif") as src,
            rasterio.open(
                tmp_path / f"{name}.asc",
                "w",
                driver="AAIGrid",
                height=40,
                width=40,
                count=1,
                dtype="float32",
                crs="EPSG:2154",
                transform=src.transform,
                nodata=-9999.0,
            ) as dst,
        ):
            dst.write(src.read())
        (tmp_path / f"{name}.tif").unlink()
    flow = make_flow(
        [
            {"id": "r", "type": "RasterReader", "params": {"path": str(tmp_path / "dalle_*.asc")}},
            {"id": "m", "type": "MergeRasters", "params": {}},
            {"id": "t", "type": "TerrainAnalysis", "params": {"products": ["slope"]}},
            {"id": "w", "type": "RasterWriter", "params": {"path": str(tmp_path / "pente.tif")}},
        ],
        [("r", "output", "m", "input"), ("m", "output", "t", "input"), ("t", "output", "w", "input")],
    )
    result = run_flow(flow, registry, tmp_path / "run")
    assert result.status == "done", result.error
    with rasterio.open(tmp_path / "pente.tif") as ds:
        assert (ds.width, ds.height, ds.crs.to_string()) == (80, 40, "EPSG:2154")
        slope = ds.read(1, masked=True)
    assert abs(float(slope[20, 40]) - np.degrees(np.arctan(0.2))) < 0.01  # la jonction des dalles ne se voit pas
    assert slope.mask[0, :].all() and slope.mask[:, 0].all()


@pytest.mark.slow
def test_memory_stays_bounded_on_a_large_raster(registry, tmp_path):
    """36 millions de pixels x 2 bandes (144 Mo) : le calcul se fait par fenêtres, la mémoire ne suit pas la taille."""
    rng = np.random.default_rng(3)
    size = 6000
    path = tmp_path / "grand.tif"
    profile = {
        "driver": "GTiff",
        "height": size,
        "width": size,
        "count": 2,
        "dtype": "uint16",
        "crs": "EPSG:2154",
        "transform": rasterio.transform.from_origin(600000, 6600000, 10, 10),
        "tiled": True,
        "blockxsize": 512,
        "blockysize": 512,
        "compress": "DEFLATE",
    }
    with rasterio.open(path, "w", **profile) as dst:
        for row in range(0, size, 500):
            block = rng.integers(1, 4000, (2, 500, size)).astype("uint16")
            dst.write(block, window=rasterio.windows.Window(0, row, size, 500))
        dst.set_band_description(1, "red")
        dst.set_band_description(2, "nir")
    flow = make_flow(
        [
            {"id": "r", "type": "RasterReader", "params": {"path": str(path)}},
            {
                "id": "c",
                "type": "RasterCalculator",
                "params": {"calculations": [{"name": "ndvi", "expression": "(nir - red) / (nir + red)"}]},
            },
            {
                "id": "w",
                "type": "RasterWriter",
                "params": {"path": str(tmp_path / "ndvi.tif"), "format": "gtiff", "overviews": False},
            },
        ],
        [("r", "output", "c", "input"), ("c", "output", "w", "input")],
    )
    process, peak, stop = psutil.Process(), [0], threading.Event()

    def watch():
        while not stop.is_set():
            peak[0] = max(peak[0], process.memory_info().rss)
            time.sleep(0.05)

    base = process.memory_info().rss
    threading.Thread(target=watch, daemon=True).start()
    started = time.perf_counter()
    result = run_flow(flow, registry, tmp_path / "run", cache=False)
    stop.set()
    assert result.status == "done", result.error
    growth_mb = (peak[0] - base) / 2**20
    print(f"NDVI sur {size}x{size} : {time.perf_counter() - started:.1f} s, pic mémoire +{growth_mb:.0f} Mo")
    assert (
        growth_mb < 700
    )  # l'image float32 entière ferait 144 Mo par bande : on reste très en dessous du tout-en-mémoire x copies
    with rasterio.open(tmp_path / "ndvi.tif") as ds:
        window = rasterio.windows.Window(2500, 2500, 100, 100)
        red, nir = rasterio.open(path).read(window=window).astype("float32")
        assert np.allclose(ds.read(1, window=window), (nir - red) / (nir + red), atol=1e-5)
