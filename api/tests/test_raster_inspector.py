"""Inspecteur de rasters : le port est une table de rasters, les pixels sont lus à la demande."""

import numpy as np
import pytest
import rasterio
from etl_api.main import create_app
from etl_engine.executor import run_flow
from etl_engine.flow import Flow
from etl_engine.registry import Registry
from etl_store import Settings
from fastapi.testclient import TestClient
from rasterio.transform import from_origin

RUN = "run0002"


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rinsp")
    y, x = np.mgrid[0:100, 0:100]
    data = np.stack([(x + y).astype("float32"), (x * 2.0).astype("float32")])
    data[0, :10, :10] = -9999.0
    path = tmp / "scene.tif"
    profile = {
        "driver": "GTiff", "height": 100, "width": 100, "count": 2, "dtype": "float32", "crs": "EPSG:2154",
        "transform": from_origin(600000, 6500100, 10, 10), "nodata": -9999.0,
    }  # fmt: skip
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data)
        dst.set_band_description(1, "a")
        dst.set_band_description(2, "b")
    flow = Flow.model_validate(
        {"nodes": [{"id": "r", "type": "RasterReader", "params": {"path": str(path)}}], "links": []}
    )
    cache = tmp / "cache"
    res = run_flow(flow, Registry.discover(), cache / RUN)
    assert res.status == "done", res.error
    settings = Settings(meta_dir=tmp / "meta", cache_dir=cache, plugins_dir=tmp / "plugins")
    with TestClient(create_app(settings)) as client:
        yield client


def url(path: str) -> str:
    return f"/api/data/{RUN}/r/output/{path}"


def tile_url(z: int, x: int, y: int, query: str = "") -> str:
    return f"/tiles/raster/{RUN}/r/output/0/{z}/{x}/{y}.png{query}"


def center_tile(z: int) -> tuple[int, int]:
    from pyproj import Transformer

    mx, my = Transformer.from_crs("EPSG:2154", "EPSG:3857", always_xy=True).transform(600500, 6499600)
    size = 2 * 20037508.342789244 / 2**z
    return int((mx + 20037508.342789244) // size), int((20037508.342789244 - my) // size)


def decode(content: bytes) -> np.ndarray:
    import io

    with rasterio.open(io.BytesIO(content)) as ds:
        return ds.read()


def test_port_is_listed_as_a_table_of_rasters(env):
    info = env.get(url("info")).json()
    assert info["total"] == 1 and "raster" in [c["name"] for c in info["columns"]]
    assert info["geometry"]["crs"] == "EPSG:2154"


def test_raster_info(env):
    info = env.get(url("raster/0")).json()
    assert (info["width"], info["height"], info["count"]) == (100, 100, 2)
    assert [b["name"] for b in info["bands"]] == ["a", "b"]
    low, high = info["display"]["ranges"]["1"]
    assert 0 <= low < high <= 198 and info["display"]["bands"] == [1]
    assert env.get(url("raster/5")).status_code == 404


def test_tile_png_is_transparent_outside_and_coloured_inside(env):
    z = 14
    x, y = center_tile(z)
    inside = env.get(tile_url(z, x, y))
    assert inside.status_code == 200 and inside.headers["content-type"] == "image/png"
    rgba = decode(inside.content)
    assert (
        rgba.shape == (4, 256, 256) and rgba[3].max() == 255 and rgba[3].min() == 0
    )  # le raster ne couvre pas toute la tuile
    outside = env.get(tile_url(z, x + 40, y))
    assert outside.status_code == 200 and decode(outside.content)[3].max() == 0


def test_tile_options_and_errors(env):
    z = 14
    x, y = center_tile(z)
    gray = decode(env.get(tile_url(z, x, y, "?palette=gray&vmin=0&vmax=198&bands=2")).content)
    colour = decode(env.get(tile_url(z, x, y, "?palette=viridis")).content)
    assert (gray[0] == gray[1]).all() and not (colour[0] == colour[1]).all()
    assert env.get(tile_url(z, x, y, "?bands=3")).status_code == 400
    assert env.get(tile_url(z, x, y, "?bands=1,2")).status_code == 400
    assert env.get(tile_url(z, x, y, "?palette=nope")).status_code == 400
    assert env.get(tile_url(2, 9, 0)).status_code == 404


def test_pixel_value_and_nodata(env):
    from pyproj import Transformer

    to_wgs = Transformer.from_crs("EPSG:2154", "EPSG:4326", always_xy=True)
    lon, lat = to_wgs.transform(600000 + 55 * 10 + 5, 6500100 - 20 * 10 - 5)  # colonne 55, ligne 20
    got = env.get(url("raster/0/pixel"), params={"lon": lon, "lat": lat}).json()
    assert got["inside"] and (got["row"], got["col"]) == (20, 55)
    assert got["values"] == {"a": 75.0, "b": 110.0}
    lon, lat = to_wgs.transform(600005, 6500095)  # coin en no-data de la bande a
    masked = env.get(url("raster/0/pixel"), params={"lon": lon, "lat": lat}).json()
    assert masked["values"]["a"] is None and masked["values"]["b"] == 0.0
    lon, lat = to_wgs.transform(700000, 6400000)
    assert env.get(url("raster/0/pixel"), params={"lon": lon, "lat": lat}).json()["inside"] is False
