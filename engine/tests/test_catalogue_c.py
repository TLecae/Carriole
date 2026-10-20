"""Catalogue, lot C : tampons, découpe, fusion, superposition, validité, coordonnées."""

import pyarrow as pa
import pytest
import shapely
from etl_engine.data import GEOM, get_crs, with_crs
from etl_engine.modifiers.buffer import Buffer
from etl_engine.modifiers.centroids import Centroids
from etl_engine.modifiers.check_validity import CheckValidity
from etl_engine.modifiers.clip import Clip
from etl_engine.modifiers.dissolve import Dissolve
from etl_engine.modifiers.extract_coordinates import ExtractCoordinates
from etl_engine.modifiers.fix_geometries import FixGeometries
from etl_engine.modifiers.points_in_polygons import PointsInPolygons
from etl_engine.modifiers.simplify import Simplify
from etl_engine.modifiers.split_by_geometry_type import SplitByGeometryType
from etl_engine.sdk import ConfigError
from etl_engine.testing import points, run_modifier, table


def col(t, name):
    return t.column(name).to_pylist()


def make(geoms, crs="EPSG:2154", **columns):
    t = pa.table({**columns, GEOM: pa.array([None if g is None else shapely.to_wkb(g) for g in geoms], pa.binary())})
    return with_crs(t, crs)


def geom_of(t, i=0):
    return shapely.from_wkb(col(t, GEOM)[i])


# ------------------------------------------------------------------------ Buffer


def test_bufferer_fixed_attribute_distance_and_negative():
    t = make(
        [shapely.Point(0, 0), shapely.box(0, 0, 10, 10), None, shapely.box(0, 0, 4, 4)],
        id=[1, 2, 3, 4],
        d=[1.0, 2.0, 1.0, 5.0],
    )
    out = run_modifier(Buffer, {"distance": 1, "quad_segs": 64}, t)
    assert geom_of(out["output"], 0).area == pytest.approx(3.14, abs=0.01)
    assert geom_of(out["output"], 1).area == pytest.approx(144 - 4 + 3.14, abs=0.05)
    assert col(out["Rejected"], "_rejection_reason") == ["pas de géométrie"]
    by_attr = run_modifier(
        Buffer, {"distance_attribute": "d", "end_cap": "flat"}, make([shapely.LineString([(0, 0), (10, 0)])], d=[2.0])
    )
    assert geom_of(by_attr["output"]).area == pytest.approx(40.0)
    shrunk = run_modifier(
        Buffer, {"distance": -3}, make([shapely.box(0, 0, 4, 4), shapely.box(0, 0, 10, 10)], id=[1, 2])
    )
    assert col(shrunk["output"], "id") == [2] and col(shrunk["Rejected"], "_rejection_reason") == ["zone tampon vide"]
    assert get_crs(out["output"]) == "EPSG:2154"


def test_bufferer_refuses_geographic_crs_and_missing_geometry():
    with pytest.raises(ConfigError, match="géographique"):
        run_modifier(Buffer, {}, points([2.0], [48.0], crs="EPSG:4326"))
    with pytest.raises(ConfigError, match="géométrie"):
        run_modifier(Buffer, {}, table(a=[1]))


# ------------------------------------------------------------------------ Dissolve / Aggregate difference


def test_dissolve_merges_touching_polygons_per_group():
    t = make(
        [shapely.box(0, 0, 1, 1), shapely.box(1, 0, 2, 1), shapely.box(10, 10, 11, 11), shapely.box(5, 5, 6, 6)],
        g=["a", "a", "a", "b"],
        nom=["n1", "n2", "n3", "n4"],
    )
    out = run_modifier(Dissolve, {"group_by": ["g"]}, t)["output"]
    rows = {r["g"]: r for r in out.to_pylist()}
    a = shapely.from_wkb(rows["a"][GEOM])
    assert a.geom_type == "MultiPolygon" and len(a.geoms) == 2 and a.area == 3  # les deux premiers sont soudés
    assert rows["a"]["_count"] == 3 and rows["b"]["nom"] == "n4" and get_crs(out) == "EPSG:2154"
    whole = run_modifier(Dissolve, {"count_attribute": ""}, make([shapely.box(0, 0, 1, 1), shapely.box(1, 0, 2, 1)]))[
        "output"
    ]
    assert geom_of(whole).geom_type == "Polygon" and "_count" not in whole.column_names


def test_dissolve_joins_lines_end_to_end():
    t = make([shapely.LineString([(0, 0), (1, 0)]), shapely.LineString([(1, 0), (2, 0)])])
    out = run_modifier(Dissolve, {}, t)["output"]
    assert geom_of(out).geom_type == "LineString" and geom_of(out).length == 2


# ------------------------------------------------------------------------ Clip


@pytest.fixture
def clip_data():
    clippee = make([shapely.box(0, 0, 10, 10), shapely.box(50, 50, 60, 60)], id=[1, 2])
    clipper = make([shapely.box(0, 0, 5, 10), shapely.box(5, 0, 8, 10)], zone=["ouest", "centre"])
    return {"input": clippee, "overlay": clipper}


def test_clip_union_inside_outside(clip_data):
    out = run_modifier(Clip, {}, clip_data)
    assert col(out["Inside"], "id") == [1] and geom_of(out["Inside"]).area == 80
    assert sorted(col(out["Outside"], "id")) == [1, 2]
    assert [g.area for g in map(shapely.from_wkb, col(out["Outside"], GEOM))] == [20.0, 100.0]


def test_clip_each_keeps_clipper_attributes(clip_data):
    out = run_modifier(Clip, {"mode": "each", "overlay_attributes": ["zone"], "prefix": "z_"}, clip_data)
    assert col(out["Inside"], "z_zone") == ["ouest", "centre"]
    assert [shapely.from_wkb(g).area for g in col(out["Inside"], GEOM)] == [50.0, 30.0]


def test_clip_edge_cases(clip_data):
    nothing = run_modifier(Clip, {}, {"input": clip_data["input"], "overlay": clip_data["overlay"].slice(0, 0)})
    assert nothing["Inside"].num_rows == 0 and nothing["Outside"].num_rows == 2
    with pytest.raises(ConfigError, match="même système"):
        run_modifier(Clip, {}, {"input": clip_data["input"], "overlay": with_crs(clip_data["overlay"], "EPSG:4326")})
    with pytest.raises(ConfigError, match="absents"):
        run_modifier(Clip, {"mode": "each", "overlay_attributes": ["nope"]}, clip_data)


# ------------------------------------------------------------------------ PointsInPolygons


def test_point_on_area_first_all_error_and_outside():
    areas = make([shapely.box(0, 0, 10, 10), shapely.box(5, 5, 15, 15)], code=["A", "B"], pop=[10, 20])
    pts = pa.concat_tables(
        [make([shapely.Point(1, 1), shapely.Point(7, 7), shapely.Point(50, 50)], id=[1, 2, 3]), make([None], id=[4])]
    )
    first = run_modifier(PointsInPolygons, {"attributes": ["code"], "prefix": "z_"}, {"points": pts, "areas": areas})
    assert col(first["Inside"], "id") == [1, 2] and col(first["Inside"], "z_code") == ["A", "A"]
    assert col(first["Outside"], "id") == [3] and col(first["Rejected"], "id") == [4]
    every = run_modifier(PointsInPolygons, {"on_multiple": "all"}, {"points": pts, "areas": areas})
    assert col(every["Inside"], "id") == [1, 2, 2] and col(every["Inside"], "code") == ["A", "A", "B"]
    assert col(every["Inside"], "pop") == [10, 10, 20]
    with pytest.raises(ConfigError, match="plusieurs polygones"):
        run_modifier(PointsInPolygons, {"on_multiple": "error"}, {"points": pts, "areas": areas})
    with pytest.raises(ConfigError, match="absents"):
        run_modifier(PointsInPolygons, {"attributes": ["nope"]}, {"points": pts, "areas": areas})


def test_point_on_area_boundary_counts_as_inside():
    areas = make([shapely.box(0, 0, 10, 10)], code=["A"])
    out = run_modifier(PointsInPolygons, {}, {"points": make([shapely.Point(10, 5)], id=[1]), "areas": areas})
    assert col(out["Inside"], "code") == ["A"]


# ------------------------------------------------------------------------ CheckValidity


def bowtie():
    return shapely.Polygon([(0, 0), (2, 2), (2, 0), (0, 2), (0, 0)])  # nœud papillon : invalide


def test_validator_splits_and_explains():
    t = make([shapely.box(0, 0, 1, 1), bowtie(), None, shapely.Polygon()], id=[1, 2, 3, 4])
    out = run_modifier(CheckValidity, {}, t)
    assert col(out["Valid"], "id") == [1]
    assert col(out["Invalid"], "id") == [2, 3, 4]
    reasons = col(out["Invalid"], "_invalid_reason")
    assert "Self-intersection" in reasons[0] and reasons[1:] == ["pas de géométrie", "géométrie vide"]
    lenient = run_modifier(CheckValidity, {"reject_empty": False}, t)
    assert col(lenient["Valid"], "id") == [1, 3, 4] and col(lenient["Invalid"], "id") == [2]


def test_validator_repair():
    t = make([shapely.box(0, 0, 1, 1), bowtie()], id=[1, 2])
    out = run_modifier(CheckValidity, {"repair": True}, t)
    assert col(out["Valid"], "id") == [1, 2] and out["Invalid"].num_rows == 0
    assert shapely.is_valid(geom_of(out["Valid"], 1)) and geom_of(out["Valid"], 1).area == pytest.approx(2.0)
    assert (
        col(out["Valid"], "_repaired_reason")[0] is None
        and "Self-intersection" in col(out["Valid"], "_repaired_reason")[1]
    )


# ------------------------------------------------------------------------ FixGeometries


def test_repairer_fixes_invalid_and_leaves_valid_alone():
    square = shapely.box(0, 0, 1, 1)
    t = make([square, bowtie()], id=[1, 2])
    out = run_modifier(FixGeometries, {}, t)
    assert col(out["output"], "id") == [1, 2] and out["Rejected"].num_rows == 0
    assert geom_of(out["output"], 0).equals(square)  # déjà valide : intacte
    fixed = geom_of(out["output"], 1)
    assert shapely.is_valid(fixed) and fixed.area == pytest.approx(2.0) and fixed.geom_type == "MultiPolygon"
    reasons = col(out["output"], "_repaired_reason")
    assert reasons[0] is None and "Self-intersection" in reasons[1]
    assert get_crs(out["output"]) == "EPSG:2154"


def test_repairer_methods_and_report_attribute_off():
    out = run_modifier(FixGeometries, {"method": "structure", "report_attribute": ""}, make([bowtie()], id=[1]))
    assert "_repaired_reason" not in out["output"].column_names
    assert shapely.is_valid(geom_of(out["output"]))


def test_repairer_rejects_missing_empty_and_collapsed_polygons():
    flat = shapely.Polygon([(0, 0), (1, 1), (2, 2), (0, 0)])  # aplati : une ligne une fois réparé
    t = make([None, shapely.Polygon(), flat, shapely.box(0, 0, 1, 1)], id=[1, 2, 3, 4])
    out = run_modifier(FixGeometries, {}, t)
    assert col(out["output"], "id") == [4]
    assert col(out["Rejected"], "id") == [1, 2, 3]
    reasons = col(out["Rejected"], "_rejection_reason")
    assert reasons[:2] == ["pas de géométrie", "géométrie vide"] and reasons[2].startswith("irréparable")
    # sans « garder le type », la ligne issue du polygone aplati est conservée
    loose = run_modifier(FixGeometries, {"keep_type": False}, t)
    assert col(loose["output"], "id") == [3, 4] and shapely.get_dimensions(geom_of(loose["output"])) == 1


def test_repairer_keeps_the_polygons_of_a_mixed_result():
    # un carré dont un second anneau pend (anneau intérieur qui sort) : la réparation donne surfaces et fragments
    odd = shapely.Polygon([(0, 0), (4, 0), (4, 4), (0, 4)], [[(1, 1), (5, 1), (5, 2), (1, 2)]])
    out = run_modifier(FixGeometries, {}, make([odd], id=[1]))["output"]
    fixed = geom_of(out)
    assert (
        shapely.is_valid(fixed)
        and shapely.get_dimensions(fixed) == 2
        and fixed.geom_type in ("Polygon", "MultiPolygon")
    )


# ------------------------------------------------------------------------ Coordinates, center, filter, simplify


def test_coordinate_extractor_sources_and_bounds():
    t = make([shapely.box(0, 0, 4, 2), shapely.LineString([(1, 1), (5, 7), (9, 3)]), None], id=[1, 2, 3])
    centre = run_modifier(ExtractCoordinates, {"prefix": "c_", "bounds": True}, t)["output"]
    assert col(centre, "c_x")[:2] == [2.0, pytest.approx(5.0, abs=2)] and col(centre, "c_y")[0] == 1.0
    assert col(centre, "c_x")[2] is None and col(centre, "c_maxx")[:2] == [4.0, 9.0] and col(centre, "c_miny")[1] == 1.0
    start = run_modifier(ExtractCoordinates, {"source": "start"}, t)["output"]
    end = run_modifier(ExtractCoordinates, {"source": "end"}, t)["output"]
    assert col(start, "x")[1] == 1.0 and col(end, "x")[1] == 9.0 and col(end, "y")[1] == 3.0
    z = run_modifier(ExtractCoordinates, {"source": "point", "z": True}, make([shapely.Point(1, 2, 3)]))["output"]
    assert col(z, "z") == [3.0]
    box = run_modifier(ExtractCoordinates, {"source": "bbox_center"}, make([shapely.box(0, 0, 4, 2)]))["output"]
    assert col(box, "x") == [2.0] and col(box, "y") == [1.0]


def test_center_point_replacer():
    crescent = shapely.Polygon([(0, 0), (10, 0), (10, 1), (1, 1), (1, 10), (0, 10)])  # en L : le centroïde est dehors
    t = make([crescent, shapely.box(0, 0, 2, 2), None], id=[1, 2, 3])
    centroid = run_modifier(Centroids, {}, t)
    assert not crescent.contains(geom_of(centroid["output"], 0)) and col(centroid["Rejected"], "id") == [3]
    inside = run_modifier(Centroids, {"method": "point_on_surface"}, t)["output"]
    assert crescent.contains(geom_of(inside, 0)) and geom_of(inside, 1).geom_type == "Point"
    assert get_crs(inside) == "EPSG:2154"


def test_geometry_filter_families():
    unit = shapely.box(0, 0, 1, 1)
    geoms = [
        shapely.Point(0, 0),
        shapely.MultiPoint([(0, 0), (1, 1)]),
        shapely.LineString([(0, 0), (1, 1)]),
        unit,
        shapely.MultiPolygon([unit]),
        shapely.GeometryCollection([shapely.Point(0, 0), unit]),
        None,
        shapely.Polygon(),
    ]
    t = make(geoms, id=list(range(8)))
    out = run_modifier(SplitByGeometryType, {}, t)
    assert col(out["Point"], "id") == [0, 1] and col(out["Line"], "id") == [2] and col(out["Area"], "id") == [3, 4]
    assert col(out["Collection"], "id") == [5] and col(out["NoGeometry"], "id") == [6, 7]


def test_simplifier_reduces_vertices_and_keeps_validity():
    wavy = shapely.LineString([(x, 0.01 * ((-1) ** x)) for x in range(100)])
    t = make([wavy, None])
    out = run_modifier(Simplify, {"tolerance": 0.5}, t)["output"]
    assert len(geom_of(out).coords) == 2 and col(out, GEOM)[1] is None and get_crs(out) == "EPSG:2154"
    with pytest.raises(ValueError):
        Simplify.Params(tolerance=0)
