import math

import numpy as np
import pyarrow as pa
import pytest
import shapely
from etl_engine.data import GEOM, get_crs, with_crs
from etl_engine.modifiers.contour import Contour
from etl_engine.modifiers.merge_rasters import MergeRasters
from etl_engine.modifiers.polygonize import Polygonize
from etl_engine.modifiers.raster_statistics import RasterStatistics
from etl_engine.modifiers.rasterize import Rasterize
from etl_engine.modifiers.reclassify_raster import ReclassifyRaster
from etl_engine.modifiers.sample_raster_values import SampleRasterValues
from etl_engine.modifiers.stack_bands import StackBands
from etl_engine.modifiers.terrain_analysis import TerrainAnalysis
from etl_engine.modifiers.zonal_statistics import ZonalStatistics
from etl_engine.raster import core
from etl_engine.sdk import ConfigError
from etl_engine.testing import points, run_modifier
from raster_support import make_raster, raster_table, read


def vector(geoms, crs="EPSG:2154", **columns):
    table = pa.table(columns) if columns else pa.table({"_n": list(range(len(geoms)))})
    return with_crs(table.append_column(GEOM, pa.array([shapely.to_wkb(g) for g in geoms], pa.binary())), crs)


# ------------------------------------------------------------------ mosaïque, composite
def test_spatial_mosaic_places_tiles_side_by_side(tmp_path):
    left = make_raster(tmp_path / "g.tif", np.full((10, 10), 1, dtype="uint8"), origin=(0.0, 100.0), nodata=0)
    right = make_raster(tmp_path / "d.tif", np.full((10, 10), 2, dtype="uint8"), origin=(100.0, 100.0), nodata=0)
    out = run_modifier(MergeRasters, {}, raster_table(left, right))["output"]
    desc = core.descriptors(out)[0]
    assert (desc["width"], desc["height"], desc["driver"]) == (20, 10, "VRT")
    data = read(out)[0]
    assert (data[:, :10] == 1).all() and (data[:, 10:] == 2).all()
    written = run_modifier(MergeRasters, {"write": True}, raster_table(left, right))["output"]
    assert core.descriptors(written)[0]["driver"] == "GTiff" and np.array_equal(read(written)[0], data)


def test_mosaic_overlap_keeps_the_last_with_data_and_groups(tmp_path):
    a = make_raster(
        tmp_path / "a.tif", np.array([[1, 1], [1, 1]], dtype="uint8"), origin=(0.0, 20.0), cell=10.0, nodata=0
    )
    b = make_raster(
        tmp_path / "b.tif", np.array([[0, 2], [2, 2]], dtype="uint8"), origin=(10.0, 20.0), cell=10.0, nodata=0
    )
    out = run_modifier(MergeRasters, {"sort_by": "rang"}, raster_table(a, b, rang=[1, 2]))["output"]
    data = read(out)[0].filled(0)
    assert data.tolist() == [[1, 1, 2], [1, 2, 2]]  # b recouvre a, sauf là où b n'a pas de donnée
    twin = make_raster(tmp_path / "c.tif", np.full((2, 2), 9, dtype="uint8"), origin=(50.0, 20.0), cell=10.0, nodata=0)
    grouped = run_modifier(MergeRasters, {"group_by": ["tuile"]}, raster_table(a, b, twin, tuile=["x", "x", "y"]))[
        "output"
    ]
    assert grouped.column("tuile").to_pylist() == ["x", "y"]


def test_mosaic_refuses_mixed_crs_or_pixel_sizes(tmp_path):
    a = make_raster(tmp_path / "a.tif", np.ones((4, 4), dtype="uint8"), cell=10.0)
    b = make_raster(tmp_path / "b.tif", np.ones((4, 4), dtype="uint8"), cell=5.0)
    with pytest.raises(ConfigError, match="même pas de pixel"):
        run_modifier(MergeRasters, {}, raster_table(a, b))
    c = make_raster(tmp_path / "c.tif", np.ones((4, 4), dtype="uint8"), crs="EPSG:3857")
    with pytest.raises(ConfigError, match="même système"):
        run_modifier(MergeRasters, {}, raster_table(a, c))


def test_temporal_composite_median_ignores_clouds(tmp_path):
    dates = [
        np.array([[10, 10], [10, 10]], dtype="float32"),
        np.array([[20, 99], [20, 20]], dtype="float32"),
        np.array([[30, 30], [30, 30]], dtype="float32"),
    ]
    dates[1][0, 1] = 99  # un nuage (valeur aberrante)
    dates[0][1, 1] = -1  # sans donnée à la première date
    paths = [make_raster(tmp_path / f"d{i}.tif", d, nodata=-1) for i, d in enumerate(dates)]
    out = run_modifier(MergeRasters, {"mode": "composite", "method": "median"}, raster_table(*paths))["output"]
    assert read(out)[0].filled(np.nan).tolist() == [[20.0, 30.0], [20.0, 25.0]]
    first = run_modifier(MergeRasters, {"mode": "composite", "method": "first"}, raster_table(*paths))["output"]
    assert read(first)[0].tolist() == [[10.0, 10.0], [10.0, 20.0]]
    other = make_raster(tmp_path / "autre.tif", np.ones((3, 3), dtype="float32"), nodata=-1)
    with pytest.raises(ConfigError, match="même grille"):
        run_modifier(MergeRasters, {"mode": "composite"}, raster_table(paths[0], other))


# ------------------------------------------------------------------ terrain
def cone(tmp_path, size=61, cell=10.0, crs="EPSG:2154"):
    """Un cône de pente 0,5 (26,57°) centré dans le raster."""
    y, x = np.mgrid[0:size, 0:size]
    z = (30 - np.hypot(x - 30, y - 30)) * cell * 0.5
    return make_raster(tmp_path / "mnt.tif", z.astype("float32"), cell=cell, crs=crs, nodata=-9999.0)


def test_terrain_slope_aspect_hillshade(tmp_path):
    mnt = cone(tmp_path)
    out = run_modifier(TerrainAnalysis, {"products": ["slope", "aspect", "hillshade", "roughness"]}, raster_table(mnt))[
        "output"
    ]
    data = read(out)
    desc = core.descriptors(out)[0]
    assert desc["band_names"] == ["slope", "aspect", "hillshade", "roughness"]
    assert data[0].mask[0, 0] and data[0].mask[-1, -1]  # le bord n'a pas de voisins
    assert abs(float(data[0][10, 30]) - math.degrees(math.atan(0.5))) < 0.2  # pente partout égale à atan(0,5)
    # au nord du sommet, la pente descend vers le nord : exposition ~ 0° ; à l'est ~ 90° ; au sud ~ 180°
    assert min(abs(float(data[1][10, 30]) - 0), abs(float(data[1][10, 30]) - 360)) < 5
    assert abs(float(data[1][30, 50]) - 90) < 5 and abs(float(data[1][50, 30]) - 180) < 5
    shade = data[2]
    assert float(shade[30, 45]) < float(shade[30, 15])  # soleil au nord-ouest (315°) : le versant ouest est plus clair
    assert float(shade.min()) >= 0 and float(shade.max()) <= 255
    percent = run_modifier(TerrainAnalysis, {"slope_unit": "percent"}, raster_table(mnt))["output"]
    assert abs(float(read(percent)[0][10, 30]) - 50.0) < 1.0


def test_terrain_on_flat_ground_and_geographic_pixels(tmp_path):
    flat = make_raster(tmp_path / "plat.tif", np.full((20, 20), 100, dtype="float32"), nodata=-9999.0)
    out = run_modifier(TerrainAnalysis, {"products": ["slope", "aspect"]}, raster_table(flat))["output"]
    data = read(out)
    assert float(data[0][5, 5]) == 0.0 and float(data[1][5, 5]) == -1.0
    y, x = np.mgrid[0:20, 0:20]
    degrees = make_raster(
        tmp_path / "wgs.tif",
        (x * 1.0).astype("float32"),
        origin=(1.0, 44.0),
        cell=0.001,
        crs="EPSG:4326",
        nodata=-9999.0,
    )
    slope = read(run_modifier(TerrainAnalysis, {}, raster_table(degrees))["output"])[0]
    expected = math.degrees(
        math.atan(1.0 / (0.001 * 111320 * math.cos(math.radians(43.99))))
    )  # 1 m de dénivelé par pixel
    assert abs(float(slope[10, 10]) - expected) < 0.1
    with pytest.raises(ConfigError, match="produit"):
        run_modifier(TerrainAnalysis, {"products": []}, raster_table(flat))


# ------------------------------------------------------------------ statistiques zonales
def test_zonal_statistics_per_polygon_and_raster(tmp_path):
    data = np.arange(100, dtype="float32").reshape(10, 10)  # 0..99, pixels de 10 m, coin haut-gauche (0, 100)
    r1 = make_raster(tmp_path / "d1.tif", data, origin=(0.0, 100.0), nodata=-1.0)
    r2 = make_raster(tmp_path / "d2.tif", data * 2, origin=(0.0, 100.0), nodata=-1.0)
    zones = vector([shapely.box(0, 80, 20, 100), shapely.box(500, 500, 600, 600)], code=["A", "B"])
    out = run_modifier(
        ZonalStatistics,
        {"stats": ["count", "mean", "max", "area"], "raster_attributes": ["date"]},
        {"zones": zones, "raster": raster_table(r1, r2, date=["j1", "j2"])},
    )["output"]
    rows = out.to_pylist()
    assert [(r["code"], r["date"]) for r in rows] == [("A", "j1"), ("B", "j1"), ("A", "j2"), ("B", "j2")]
    a1 = rows[0]
    assert (
        a1["b1_count"] == 4 and a1["b1_mean"] == (0 + 1 + 10 + 11) / 4 and a1["b1_max"] == 11 and a1["b1_area"] == 400
    )
    assert rows[1]["b1_count"] == 0 and rows[1]["b1_mean"] is None  # hors du raster
    assert rows[2]["b1_mean"] == 2 * a1["b1_mean"]
    assert get_crs(out) == "EPSG:2154"


def test_zonal_categories_nodata_and_all_touched(tmp_path):
    classes = np.array([[1, 1, 2], [1, 3, 3], [0, 3, 3]], dtype="uint8")
    r = make_raster(tmp_path / "c.tif", classes, origin=(0.0, 30.0), nodata=0, names=["classe"])
    zone = vector([shapely.box(0, 0, 30, 30)])
    out = run_modifier(
        ZonalStatistics,
        {"bands": ["classe"], "stats": ["majority", "unique", "count"]},
        {"zones": zone, "raster": raster_table(r)},
    )["output"]
    row = out.to_pylist()[0]
    assert (row["classe_majority"], row["classe_unique"], row["classe_count"]) == (
        3.0,
        3.0,
        8.0,
    )  # le 0 est sans donnée
    small = vector([shapely.box(1, 21, 2, 22)])
    centre = run_modifier(ZonalStatistics, {"stats": ["count"]}, {"zones": small, "raster": raster_table(r)})[
        "output"
    ].to_pylist()[0]
    touched = run_modifier(
        ZonalStatistics, {"stats": ["count"], "all_touched": True}, {"zones": small, "raster": raster_table(r)}
    )["output"].to_pylist()[0]
    assert (centre["classe_count"], touched["classe_count"]) == (0.0, 1.0)


def test_zonal_reprojects_zones(tmp_path):
    from pyproj import Transformer

    r = make_raster(tmp_path / "v.tif", np.full((10, 10), 5, dtype="float32"), origin=(600000.0, 6500100.0))
    to_wgs = Transformer.from_crs("EPSG:2154", "EPSG:4326", always_xy=True)
    x0, y0 = to_wgs.transform(600020, 6500020)
    x1, y1 = to_wgs.transform(600080, 6500080)
    out = run_modifier(
        ZonalStatistics,
        {"stats": ["mean", "count"]},
        {"zones": vector([shapely.box(x0, y0, x1, y1)], crs="EPSG:4326"), "raster": raster_table(r)},
    )["output"]
    assert out.to_pylist()[0]["b1_mean"] == 5.0 and out.to_pylist()[0]["b1_count"] >= 30


# ------------------------------------------------------------------ valeurs sous des points
def test_point_sampler_reads_values_and_reports_outside_points(tmp_path):
    data = np.arange(25, dtype="float32").reshape(5, 5)
    r = make_raster(tmp_path / "s.tif", data, origin=(0.0, 50.0), nodata=-1.0, names=["alt"])
    pts = points([5.0, 45.0, 25.0, 500.0], [45.0, 5.0, 25.0, 500.0], nom=["nw", "se", "centre", "loin"])
    out = run_modifier(SampleRasterValues, {"prefix": "v_"}, {"points": pts, "raster": raster_table(r)})
    rows = out["output"].to_pylist()
    assert {r["nom"]: r["v_alt"] for r in rows} == {"nw": 0.0, "se": 24.0, "centre": 12.0}
    assert out["Outside"].column("nom").to_pylist() == ["loin"]


def test_point_sampler_uses_the_first_raster_covering_each_point_and_reprojects(tmp_path):
    from pyproj import Transformer

    a = make_raster(tmp_path / "a.tif", np.full((5, 5), 1, dtype="float32"), origin=(600000.0, 6500050.0))
    b = make_raster(tmp_path / "b.tif", np.full((5, 5), 2, dtype="float32"), origin=(600050.0, 6500050.0))
    to_wgs = Transformer.from_crs("EPSG:2154", "EPSG:4326", always_xy=True)
    lon_a, lat_a = to_wgs.transform(600025, 6500025)
    lon_b, lat_b = to_wgs.transform(600075, 6500025)
    pts = points([lon_a, lon_b], [lat_a, lat_b], crs="EPSG:4326", nom=["a", "b"])
    out = run_modifier(SampleRasterValues, {}, {"points": pts, "raster": raster_table(a, b)})["output"]
    assert (
        out.to_pylist()[0]["b1"] == 1.0
        and out.to_pylist()[1]["b1"] == 2.0
        and out.column("_raster").to_pylist() == [0, 1]
    )


# ------------------------------------------------------------------ vectorisation, rastérisation
def test_polygonizer_makes_a_polygon_per_connected_area(tmp_path):
    classes = np.array([[1, 1, 2, 2], [1, 0, 2, 2], [3, 3, 0, 1]], dtype="uint8")
    r = make_raster(tmp_path / "c.tif", classes, origin=(0.0, 30.0), nodata=0)
    out = run_modifier(Polygonize, {"value_attribute": "classe"}, raster_table(r, nom=["x"]))["output"]
    rows = out.to_pylist()
    by_value = {}
    for row, g in zip(rows, [shapely.from_wkb(w) for w in out.column(GEOM).to_pylist()], strict=True):
        by_value.setdefault(row["classe"], []).append(round(g.area))
    assert by_value == {1.0: [300, 100], 2.0: [400], 3.0: [200]} or {k: sorted(v) for k, v in by_value.items()} == {
        1.0: [100, 300],
        2.0: [400],
        3.0: [200],
    }
    assert (
        get_crs(out) == "EPSG:2154"
        and set(out.column("nom").to_pylist()) == {"x"}
        and out.column("band").to_pylist()[0] == 1
    )
    big = run_modifier(Polygonize, {"min_area": 150}, raster_table(r))["output"]
    assert min(shapely.from_wkb(w).area for w in big.column(GEOM).to_pylist()) >= 150


def test_rasterizer_burns_attribute_values_and_fixed_masks():
    zones = vector([shapely.box(0, 0, 20, 20), shapely.box(10, 10, 30, 30)], code=[5, 7])
    out = run_modifier(
        Rasterize, {"value_attribute": "code", "cell_size": 10, "dtype": "int16", "background": 0}, zones
    )["output"]
    desc = core.descriptors(out)[0]
    assert (desc["width"], desc["height"], desc["crs"]) == (3, 3, "EPSG:2154") and desc["band_names"] == ["code"]
    assert read(out)[0].filled(-1).tolist() == [[-1, 7, 7], [5, 7, 7], [5, 5, -1]]  # le fond est « sans donnée »
    mask = run_modifier(Rasterize, {"cell_size": 10, "dtype": "uint8"}, zones)["output"]
    assert set(np.unique(read(mask)[0].compressed())) == {1}
    lines = vector([shapely.LineString([(0, 5), (30, 5)])])
    thin = run_modifier(Rasterize, {"cell_size": 10, "all_touched": True, "extent": [0, 0, 30, 30]}, lines)["output"]
    assert read(thin)[0].compressed().size == 3
    with pytest.raises(ConfigError, match="attribut absent"):
        run_modifier(Rasterize, {"value_attribute": "nope"}, zones)


def test_rasterize_then_polygonize_round_trip():
    zones = vector([shapely.box(0, 0, 40, 40)], code=[3])
    raster = run_modifier(Rasterize, {"value_attribute": "code", "cell_size": 5}, zones)["output"]
    back = run_modifier(Polygonize, {}, raster)["output"]
    assert back.num_rows == 1 and round(shapely.from_wkb(back.column(GEOM)[0].as_py()).area) == 1600


# ------------------------------------------------------------------ statistiques, empilement, reclassement, courbes
def test_raster_statistics_are_exact_and_added_as_attributes(tmp_path):
    data = np.arange(1, 10001, dtype="float32").reshape(100, 100)
    data[0, 0] = -1.0
    r = make_raster(tmp_path / "s.tif", data, nodata=-1.0, names=["v"])
    out = run_modifier(
        RasterStatistics,
        {"stats": ["min", "max", "mean", "std", "count", "nodata_count", "p50"]},
        raster_table(r, nom=["x"]),
    )["output"]
    row = out.to_pylist()[0]
    values = np.arange(2, 10001, dtype="float64")
    assert (
        row["nom"] == "x"
        and row["v_min"] == 2
        and row["v_max"] == 10000
        and row["v_count"] == 9999
        and row["v_nodata_count"] == 1
    )
    assert abs(row["v_mean"] - values.mean()) < 1e-6 and abs(row["v_std"] - values.std()) < 1e-3
    assert abs(row["v_p50"] - np.median(values)) < 60


def test_band_stacker_makes_a_virtual_stack_or_resamples_when_grids_differ(tmp_path):
    red = make_raster(tmp_path / "B04.tif", np.full((10, 10), 4, dtype="uint16"))
    nir = make_raster(tmp_path / "B08.tif", np.full((10, 10), 8, dtype="uint16"))
    out = run_modifier(StackBands, {"name_attribute": "nom"}, raster_table(red, nir, nom=["red", "nir"]))["output"]
    desc = core.descriptors(out)[0]
    assert desc["driver"] == "VRT" and desc["band_names"] == ["red", "nir"] and read(out)[:, 5, 5].tolist() == [4, 8]
    coarse = make_raster(tmp_path / "B05.tif", np.full((5, 5), 5, dtype="uint16"), cell=20.0)
    mixed = run_modifier(StackBands, {"names": ["a", "b"], "resampling": "nearest"}, raster_table(red, coarse))[
        "output"
    ]
    mdesc = core.descriptors(mixed)[0]
    assert (
        mdesc["driver"] == "GTiff"
        and mdesc["width"] == 10
        and mdesc["band_names"] == ["a", "b"]
        and read(mixed)[:, 3, 3].tolist() == [4, 5]
    )


def test_reclassifier_ranges_exact_values_and_others(tmp_path):
    ndvi = np.array([[-0.5, 0.1, 0.25], [0.5, 0.8, np.nan]], dtype="float32")
    r = make_raster(tmp_path / "n.tif", ndvi, nodata=float("nan"))
    rules = [{"max": 0.0, "value": 1}, {"min": 0.0, "max": 0.3, "value": 2}, {"min": 0.3, "value": 3}]
    out = run_modifier(ReclassifyRaster, {"rules": rules}, raster_table(r))["output"]
    assert core.descriptors(out)[0]["band_names"] == ["class"]
    assert read(out)[0].filled(255).tolist() == [[1, 2, 2], [3, 3, 255]]
    exact = run_modifier(
        ReclassifyRaster,
        {"rules": [{"min": 0.8, "max": 0.8, "include_max": True, "value": 9}], "others": "default", "default_value": 0},
        raster_table(r),
    )["output"]
    assert read(exact)[0].filled(255).tolist() == [[0, 0, 0], [0, 9, 255]]
    with pytest.raises(ConfigError, match="au moins une règle"):
        run_modifier(ReclassifyRaster, {}, raster_table(r))


def test_contours_follow_the_terrain(tmp_path):
    mnt = cone(tmp_path, size=61)
    out = run_modifier(Contour, {"interval": 50.0, "base": 0.0}, raster_table(mnt, nom=["m"]))["output"]
    levels = sorted(set(out.column("elevation").to_pylist()))
    assert {0.0, 50.0, 100.0} <= set(levels) and all(level % 50 == 0 for level in levels)
    line = shapely.from_wkb(out.column(GEOM)[out.column("elevation").to_pylist().index(100.0)].as_py())
    centre = shapely.Point(600305.0, 6500100.0 - 305.0)
    radius = (30 - 100 / 5) * 10  # z = (30 - r) * 5 = 100 -> r = 10 pixels = 100 m
    assert (
        abs(line.distance(centre) - radius) < 15
        and get_crs(out) == "EPSG:2154"
        and set(out.column("nom").to_pylist()) == {"m"}
    )
    explicit = run_modifier(Contour, {"levels": [125.0]}, raster_table(mnt))["output"]
    assert set(explicit.column("elevation").to_pylist()) == {125.0}
