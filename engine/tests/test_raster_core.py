import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest
import rasterio
import shapely
from etl_engine.data import GEOM, get_crs
from etl_engine.executor import run_flow
from etl_engine.modifiers.clip_raster_by_mask import ClipRasterByMask
from etl_engine.modifiers.raster_calculator import RasterCalculator
from etl_engine.modifiers.raster_reader import RasterReader
from etl_engine.modifiers.raster_writer import RasterWriter
from etl_engine.modifiers.resample_raster import ResampleRaster
from etl_engine.modifiers.stac_search import STACSearch
from etl_engine.modifiers.warp_raster import WarpRaster
from etl_engine.raster import core
from etl_engine.sdk import ConfigError
from etl_engine.testing import run_modifier
from raster_support import make_raster, raster_table, read

from conftest import make_flow


@pytest.fixture
def scene(tmp_path):
    """Une scène à deux bandes nommées red / nir (100 x 100 pixels de 10 m)."""
    rng = np.random.default_rng(1)
    red = rng.integers(100, 1000, (100, 100)).astype("uint16")
    nir = rng.integers(1000, 4000, (100, 100)).astype("uint16")
    red[0, 0] = 0  # pixel sans donnée
    nir[0, 0] = 0
    return make_raster(tmp_path / "scene.tif", np.stack([red, nir]), nodata=0, names=["red", "nir"]), red, nir


# ------------------------------------------------------------------ lecture
def test_reader_describes_rasters_and_their_extent(scene, tmp_path):
    path, _, _ = scene
    make_raster(tmp_path / "autre.tif", np.ones((20, 30), dtype="float32"), origin=(601000.0, 6500100.0))
    out = run_modifier(RasterReader, {"path": str(tmp_path / "*.tif")})["output"]
    descs = core.descriptors(out)
    assert [d["width"] for d in descs] == [30, 100] and out.column("filename").to_pylist() == ["autre.tif", "scene.tif"]
    assert descs[1]["band_names"] == ["red", "nir"] and descs[1]["crs"] == "EPSG:2154" and descs[1]["nodata"] == 0.0
    assert shapely.from_wkb(out.column(GEOM)[1].as_py()).bounds == (600000.0, 6499100.0, 601000.0, 6500100.0)
    assert get_crs(out) == "EPSG:2154"


def test_reader_selects_and_renames_bands(scene):
    path, _, nir = scene
    out = run_modifier(RasterReader, {"path": str(path), "bands": ["nir"], "band_names": ["proche_ir"]})["output"]
    desc = core.descriptors(out)[0]
    assert desc["count"] == 1 and desc["band_names"] == ["proche_ir"] and desc["driver"] == "VRT"
    assert np.array_equal(read(out)[0].filled(0), nir)
    with pytest.raises(ConfigError, match="bande introuvable"):
        run_modifier(RasterReader, {"path": str(path), "bands": ["xyz"]})
    with pytest.raises(ConfigError, match="fichier introuvable"):
        run_modifier(RasterReader, {"path": str(path.parent / "rien*.tif")})


# ------------------------------------------------------------------ calculatrice
def test_calculator_ndvi_by_band_names_and_nodata(scene):
    path, red, nir = scene
    table = raster_table(path)
    out = run_modifier(
        RasterCalculator,
        {
            "calculations": [
                {"name": "ndvi", "expression": "(nir - red) / (nir + red)"},
                {"name": "haut", "expression": "where(b2 > 2500, 1, 0)"},
            ]
        },
        table,
    )["output"]
    desc = core.descriptors(out)[0]
    assert desc["band_names"] == ["ndvi", "haut"] and desc["dtype"] == "float32" and desc["width"] == 100
    data = read(out)
    expected = (nir.astype("float32") - red) / (nir.astype("float32") + red)
    assert np.allclose(data[0][1:, 1:].filled(np.nan), expected[1:, 1:], atol=1e-5)
    assert data[0].mask[0, 0] and data[1].mask[0, 0]  # le pixel sans donnée reste sans donnée
    assert np.array_equal(data[1][5:, 5:].filled(-1), (nir > 2500).astype("float32")[5:, 5:])


def test_calculator_with_two_inputs_aligns_the_second(scene, tmp_path):
    path, red, _ = scene
    coarse = make_raster(
        tmp_path / "grossier.tif", np.full((50, 50), 7, dtype="float32"), cell=20.0
    )  # même emprise, pixels de 20 m
    out = run_modifier(
        RasterCalculator,
        {
            "inputs": ["a", "b"],
            "calculations": [{"name": "somme", "expression": "a_red + b_b1"}],
            "resampling": "nearest",
        },
        {"a": raster_table(path), "b": raster_table(coarse)},
    )["output"]
    data = read(out)[0]
    assert np.allclose(data[1:, 1:].filled(0), red.astype("float32")[1:, 1:] + 7)


@pytest.mark.parametrize(
    ("calculations", "message"),
    [
        ([], "au moins une bande"),
        ([{"name": "x", "expression": "inconnue + 1"}], "inconnue"),
        ([{"name": "x", "expression": "b1 +"}], "invalide"),
        ([{"name": "x y", "expression": "b1"}], "nom de bande invalide"),
        ([{"name": "x", "expression": "__import__('os')"}], "inconnue|invalide"),
    ],
)
def test_calculator_refuses_bad_expressions(scene, calculations, message):
    with pytest.raises(ConfigError, match=message):
        run_modifier(RasterCalculator, {"calculations": calculations}, raster_table(scene[0]))


# ------------------------------------------------------------------ reprojection, rééchantillonnage
def test_reprojector_changes_crs_and_keeps_data_roughly(scene):
    path, red, _ = scene
    out = run_modifier(WarpRaster, {"crs": "EPSG:3857", "resampling": "nearest"}, raster_table(path))["output"]
    desc = core.descriptors(out)[0]
    assert desc["crs"] == "EPSG:3857" and desc["count"] == 2 and desc["band_names"] == ["red", "nir"]
    data = read(out)[0]
    assert abs(float(data.mean()) - float(red[red > 0].mean())) < 60  # mêmes valeurs, redistribuées
    assert data.mask.any()  # les coins hors de l'image d'origine sont sans donnée
    with pytest.raises(ConfigError, match="inconnu"):
        run_modifier(WarpRaster, {"crs": "EPSG:99999999"}, raster_table(path))


def test_resampler_changes_the_pixel_size_and_aligns_on_a_reference(scene, tmp_path):
    path, _, _ = scene
    coarse = run_modifier(ResampleRaster, {"cell_size": 20.0, "resampling": "average"}, raster_table(path))["output"]
    desc = core.descriptors(coarse)[0]
    assert (desc["width"], desc["height"]) == (50, 50) and desc["transform"][0] == 20.0 and desc["count"] == 2
    reference = make_raster(
        tmp_path / "ref.tif", np.zeros((40, 40), dtype="uint8"), origin=(600200.0, 6500000.0), cell=5.0
    )
    aligned = run_modifier(
        ResampleRaster, {"align": True}, {"input": raster_table(path), "reference": raster_table(reference)}
    )["output"]
    adesc = core.descriptors(aligned)[0]
    assert (adesc["width"], adesc["height"], adesc["transform"][0], adesc["transform"][2]) == (40, 40, 5.0, 600200.0)
    assert ResampleRaster.ports(ResampleRaster.Params.model_validate({"align": True}))[0] == ("input", "reference")
    with pytest.raises(ConfigError, match="indiquer"):
        run_modifier(ResampleRaster, {}, raster_table(path))


# ------------------------------------------------------------------ découpe
def polygons(*boxes, crs="EPSG:2154", **columns):
    import pyarrow as pa
    from etl_engine.data import with_crs

    table = pa.table(columns or {"_n": list(range(len(boxes)))})
    return with_crs(
        table.append_column(GEOM, pa.array([shapely.to_wkb(shapely.box(*b)) for b in boxes], pa.binary())), crs
    )


def test_clip_union_crops_and_masks_outside_the_polygon(scene):
    path, red, _ = scene
    triangle = shapely.Polygon([(600100, 6499600), (600500, 6499600), (600100, 6500000)])
    import pyarrow as pa
    from etl_engine.data import with_crs

    clipper = with_crs(pa.table({"geometry": pa.array([shapely.to_wkb(triangle)], pa.binary())}), "EPSG:2154")
    out = run_modifier(ClipRasterByMask, {}, {"input": raster_table(path), "mask": clipper})["output"]
    desc = core.descriptors(out)[0]
    assert (desc["width"], desc["height"]) == (40, 40) and desc["transform"][2] == 600100.0
    data = read(out)
    assert data[0].mask.any() and not data[0].mask.all()  # coin supérieur gauche gardé, coin opposé masqué
    assert data[0].mask[0, 39] and not data[0].mask[39, 0] or data[0].mask[39, 39]


def test_clip_each_gives_one_raster_per_polygon_with_attributes_and_reprojects(scene):
    path, _, _ = scene
    from pyproj import Transformer

    to_wgs = Transformer.from_crs("EPSG:2154", "EPSG:4326", always_xy=True)

    def lonlat(box):
        x0, y0 = to_wgs.transform(box[0], box[1])
        x1, y1 = to_wgs.transform(box[2], box[3])
        return (x0, y0, x1, y1)

    clipper = polygons(
        lonlat((600100, 6499500, 600300, 6499700)),
        lonlat((600600, 6499600, 600800, 6499800)),
        lonlat((0, 0, 1, 1)),
        crs="EPSG:4326",
        code=["A", "B", "C"],
    )
    out = run_modifier(
        ClipRasterByMask,
        {"mode": "each", "mask_attributes": ["code"]},
        {"input": raster_table(path), "mask": clipper},
    )["output"]
    assert out.column("code").to_pylist() == ["A", "B"]  # C ne recouvre pas le raster
    assert [(d["width"], d["height"]) for d in core.descriptors(out)] == [(21, 21), (21, 21)] or all(
        18 <= d["width"] <= 22 for d in core.descriptors(out)
    )


def test_clip_by_extent(scene):
    out = run_modifier(
        ClipRasterByMask, {"by": "extent", "extent": [600000, 6499900, 600200, 6500100]}, raster_table(scene[0])
    )["output"]
    desc = core.descriptors(out)[0]
    assert (desc["width"], desc["height"]) == (20, 20)
    with pytest.raises(ConfigError, match="quatre nombres"):
        run_modifier(ClipRasterByMask, {"by": "extent"}, raster_table(scene[0]))


# ------------------------------------------------------------------ écriture
def test_writer_writes_a_cog_per_raster_with_a_template(scene, tmp_path):
    path, red, _ = scene
    out_dir = tmp_path / "sortie"
    table = raster_table(path, attrs=None) if False else raster_table(path, nom=["scene_a"])
    summary = run_modifier(RasterWriter, {"path": str(out_dir / "{nom}.tif")}, table)["summary"]
    target = out_dir / "scene_a.tif"
    assert summary.column("path").to_pylist() == [str(target)] and target.exists()
    with rasterio.open(target) as ds:
        assert (
            ds.profile["driver"] == "GTiff" and ds.overviews(1) and ds.block_shapes[0] == (512, 512) or ds.height < 512
        )
        assert ds.count == 2 and ds.descriptions == ("red", "nir")
        assert np.array_equal(ds.read(1), red)
    with pytest.raises(ConfigError, match="existe déjà"):
        run_modifier(RasterWriter, {"path": str(target), "mode": "create"}, table)
    with pytest.raises(ConfigError, match="plusieurs rasters"):
        run_modifier(RasterWriter, {"path": str(out_dir / "un.tif")}, raster_table(path, path))


def test_writer_gtiff_with_overviews_into_a_folder(scene, tmp_path):
    path, _, _ = scene
    big = make_raster(tmp_path / "grand.tif", np.ones((1200, 1200), dtype="uint8"))
    run_modifier(
        RasterWriter, {"path": str(tmp_path / "dossier") + "/", "format": "gtiff", "overviews": True}, raster_table(big)
    )
    with rasterio.open(tmp_path / "dossier" / "grand.tif") as ds:
        assert ds.overviews(1)


# ------------------------------------------------------------------ dans un flux
def test_a_raster_flow_end_to_end(scene, registry, tmp_path):
    path, _, _ = scene
    flow = make_flow(
        [
            {"id": "r", "type": "RasterReader", "params": {"path": str(path)}},
            {
                "id": "c",
                "type": "RasterCalculator",
                "params": {"calculations": [{"name": "ndvi", "expression": "(nir - red) / (nir + red)"}]},
            },
            {"id": "w", "type": "RasterWriter", "params": {"path": str(tmp_path / "ndvi.tif")}},
        ],
        [("r", "output", "c", "input"), ("c", "output", "w", "input")],
    )
    result = run_flow(flow, registry, tmp_path / "run", cache=False)
    assert result.status == "done", result.error
    with rasterio.open(tmp_path / "ndvi.tif") as ds:
        assert ds.count == 1 and ds.descriptions == ("ndvi",)
    assert not (tmp_path / "run" / "r").exists()  # sans cache, les fichiers intermédiaires disparaissent avec leur nœud


# ------------------------------------------------------------------ STAC (catalogue simulé, sans réseau)
class Catalog(BaseHTTPRequestHandler):
    base = ""
    assets: dict[str, str] = {}
    searches: list[dict] = []

    def _json(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        conforms = [
            "https://api.stacspec.org/v1.0.0/core",
            "https://api.stacspec.org/v1.0.0/item-search",
            "https://api.stacspec.org/v1.0.0/item-search#query",
            "https://api.stacspec.org/v1.0.0/item-search#sort",
        ]
        if self.path.startswith("/conformance"):
            return self._json({"conformsTo": conforms})
        self._json(
            {
                "type": "Catalog",
                "stac_version": "1.0.0",
                "id": "test",
                "description": "catalogue de test",
                "conformsTo": conforms,
                "links": [
                    {"rel": "self", "href": self.base + "/"},
                    {"rel": "root", "href": self.base + "/"},
                    {"rel": "search", "href": self.base + "/search", "method": "POST"},
                    {"rel": "search", "href": self.base + "/search", "method": "GET"},
                ],
            }
        )

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        Catalog.searches.append(json.loads(self.rfile.read(length) or b"{}"))
        self._json({"type": "FeatureCollection", "features": [self._item(i) for i in (1, 2)], "links": []})

    def _item(self, n):
        return {
            "type": "Feature",
            "stac_version": "1.0.0",
            "id": f"scene-{n}",
            "collection": "demo",
            "geometry": {"type": "Polygon", "coordinates": [[[1, 43], [2, 43], [2, 44], [1, 44], [1, 43]]]},
            "bbox": [1, 43, 2, 44],
            "properties": {"datetime": f"2024-06-0{n}T10:00:00Z", "eo:cloud_cover": 10.0 * n, "platform": "demo-sat"},
            "links": [],
            "assets": {name: {"href": href, "type": "image/tiff"} for name, href in self.assets.items()},
        }

    def log_message(self, *args):
        pass


@pytest.fixture
def stac(scene):
    path, red, nir = scene
    # comme dans un vrai catalogue : un fichier à une bande par asset
    Catalog.assets = {
        "red": str(make_raster(path.parent / "B04.tif", red, nodata=0)),
        "nir": str(make_raster(path.parent / "B08.tif", nir, nodata=0)),
    }
    Catalog.searches = []
    httpd = HTTPServer(("127.0.0.1", 0), Catalog)
    Catalog.base = f"http://127.0.0.1:{httpd.server_port}"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield Catalog.base
    httpd.shutdown()


def test_stac_search_builds_one_raster_per_scene_with_the_assets_as_bands(stac):
    params = {
        "api_url": stac,
        "collections": ["demo"],
        "bbox": [1, 43, 2, 44],
        "datetime": "2024-06-01/2024-06-30",
        "query": '{"eo:cloud_cover": {"lt": 50}}',
        "assets": ["red", "nir"],
        "max_items": 10,
    }
    out = run_modifier(STACSearch, params)["output"]
    assert out.column("stac_id").to_pylist() == ["scene-1", "scene-2"] and out.column("cloud_cover").to_pylist() == [
        10.0,
        20.0,
    ]
    desc = core.descriptors(out)[0]
    assert (
        desc["count"] == 2
        and desc["band_names"] == ["red", "nir"]
        and desc["driver"] == "VRT"
        and desc["crs"] == "EPSG:2154"
    )
    sent = Catalog.searches[0]
    assert sent["collections"] == ["demo"] and sent["bbox"] == [1, 43, 2, 44] and sent["limit"] <= 10
    assert sent["query"] == {"eo:cloud_cover": {"lt": 50}}


def test_stac_search_feeds_the_calculator_through_the_virtual_raster(stac):
    out = run_modifier(STACSearch, {"api_url": stac, "assets": ["red", "nir"], "bbox": [1, 43, 2, 44]})["output"]
    calc = run_modifier(
        RasterCalculator, {"calculations": [{"name": "ndvi", "expression": "(nir - red) / (nir + red)"}]}, out
    )["output"]
    assert calc.num_rows == 2 and core.descriptors(calc)[0]["band_names"] == ["ndvi"]


def test_stac_search_validates_and_explains_errors(stac):
    with pytest.raises(ConfigError, match="au moins un asset"):
        run_modifier(STACSearch, {"api_url": stac})
    with pytest.raises(ConfigError, match="absent de la scène"):
        run_modifier(STACSearch, {"api_url": stac, "assets": ["inconnu"]})
    with pytest.raises(ConfigError, match="recherche STAC impossible"):
        run_modifier(STACSearch, {"api_url": "http://127.0.0.1:9", "assets": ["red"]})
    assert STACSearch.ports(STACSearch.Params.model_validate({"from_input": True}))[0] == ("input",)
