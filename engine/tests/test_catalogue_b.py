"""Catalogue, lot B : relations spatiales, agrégation, tables de correspondance, listes, géométries."""

import pyarrow as pa
import pytest
import shapely
from etl_engine.data import GEOM, get_crs, with_crs
from etl_engine.modifiers.aggregate import Aggregate
from etl_engine.modifiers.explode_list import ExplodeList
from etl_engine.modifiers.extract_by_location import ExtractByLocation
from etl_engine.modifiers.geometry_from_attribute import GeometryFromAttribute
from etl_engine.modifiers.geometry_to_attribute import GeometryToAttribute
from etl_engine.modifiers.join_by_location import JoinByLocation
from etl_engine.modifiers.map_values import MapValues
from etl_engine.modifiers.measure_area import MeasureArea
from etl_engine.modifiers.point_from_xy import PointFromXY
from etl_engine.modifiers.replace_nulls import ReplaceNulls
from etl_engine.modifiers.split_text import SplitText
from etl_engine.sdk import ConfigError
from etl_engine.testing import points, run_modifier, table


def col(t, name):
    return t.column(name).to_pylist()


def polygons(boxes, crs="EPSG:2154", **columns):
    geoms = [shapely.box(*b) for b in boxes]
    t = pa.table({**columns, GEOM: pa.array(shapely.to_wkb(geoms), pa.binary())})
    return with_crs(t, crs)


# ------------------------------------------------------------------------ ExtractByLocation / Relator


@pytest.fixture
def zones():
    return polygons([(0, 0, 10, 10), (20, 0, 30, 10)], nom=["A", "B"])


def test_spatial_filter_passed_failed_rejected(zones):
    cand = points([5, 25, 15, 1], [5, 5, 5, 1], crs="EPSG:2154", id=[1, 2, 3, 4])
    cand = pa.concat_tables([cand, pa.table({"id": [5], GEOM: pa.array([None], pa.binary())})])
    out = run_modifier(ExtractByLocation, {"predicate": "within"}, {"candidate": cand, "filter": zones})
    assert col(out["Passed"], "id") == [1, 2, 4] and col(out["Failed"], "id") == [3]
    assert col(out["Rejected"], "_rejection_reason") == ["pas de géométrie"]
    inverted = run_modifier(
        ExtractByLocation, {"predicate": "within", "invert": True}, {"candidate": cand, "filter": zones}
    )
    assert col(inverted["Passed"], "id") == [3] and col(inverted["Failed"], "id") == [1, 2, 4]


def test_spatial_filter_dwithin_and_no_filter_features(zones):
    cand = points([12, 40], [5, 5], id=[1, 2])
    near = run_modifier(
        ExtractByLocation, {"predicate": "dwithin", "distance": 3}, {"candidate": cand, "filter": zones}
    )
    assert col(near["Passed"], "id") == [1] and col(near["Failed"], "id") == [2]
    empty = run_modifier(ExtractByLocation, {}, {"candidate": cand, "filter": zones.slice(0, 0)})
    assert empty["Passed"].num_rows == 0 and empty["Failed"].num_rows == 2


def test_spatial_filter_crs_mismatch_is_refused(zones):
    cand = points([5], [5], crs="EPSG:4326", id=[1])
    with pytest.raises(ConfigError, match="même système"):
        run_modifier(ExtractByLocation, {}, {"candidate": cand, "filter": zones})


def test_spatial_relator_counts_and_lists(zones):
    base = polygons([(0, 0, 15, 5), (100, 100, 101, 101)], nom=["x", "y"])
    related = points([5, 8, 25, 50], [2, 2, 2, 50], nom=["p1", "p2", "p3", "p4"])
    out = run_modifier(
        JoinByLocation,
        {"list_attribute": "nom", "separator": "|"},
        {"base": base, "related": related},
    )["output"]
    assert col(out, "_related_count") == [2, 0]
    assert col(out, "_related_values") == ["p1|p2", None]
    assert get_crs(out) == "EPSG:2154"


def test_spatial_relator_counts_polygon_polygon_and_missing_geometry(zones):
    base = pa.concat_tables([zones, pa.table({"nom": ["C"], GEOM: pa.array([None], pa.binary())})])
    related = polygons([(5, 5, 25, 6)])
    out = run_modifier(JoinByLocation, {"count_attribute": "n"}, {"base": base, "related": related})
    assert col(out["output"], "n") == [1, 1] and col(out["Rejected"], "nom") == ["C"]


# ------------------------------------------------------------------------ Aggregate


def test_aggregate_collects_geometries_counts_and_sums():
    t = points([0, 1, 2, 3], [0, 0, 0, 0], g=["a", "a", "b", "a"], v=[1, 2, 3, None], nom=["n1", "n2", "n3", "n4"])
    out = run_modifier(Aggregate, {"group_by": ["g"], "sum_attributes": ["v"]}, t)["output"]
    rows = {r["g"]: r for r in out.to_pylist()}
    assert rows["a"]["_count"] == 3 and rows["a"]["v_sum"] == 3.0 and rows["a"]["nom"] == "n1"
    assert (
        shapely.from_wkb(rows["a"][GEOM]).geom_type == "MultiPoint"
        and len(shapely.from_wkb(rows["a"][GEOM]).geoms) == 3
    )
    assert shapely.from_wkb(rows["b"][GEOM]).geom_type == "MultiPoint" and get_crs(out) == "EPSG:2154"


def test_aggregate_mixed_geometries_and_options():
    mixed = pa.table(
        {
            "g": ["x", "x"],
            GEOM: pa.array([shapely.to_wkb(shapely.Point(0, 0)), shapely.to_wkb(shapely.box(0, 0, 1, 1))], pa.binary()),
        }
    )
    out = run_modifier(Aggregate, {"group_by": ["g"]}, mixed)["output"]
    assert shapely.from_wkb(col(out, GEOM)[0]).geom_type == "GeometryCollection"
    first = run_modifier(Aggregate, {"group_by": ["g"], "geometry": "first"}, mixed)["output"]
    assert shapely.from_wkb(col(first, GEOM)[0]).geom_type == "Point"
    none = run_modifier(Aggregate, {"geometry": "none", "count_attribute": ""}, mixed)["output"]
    assert GEOM not in none.column_names and none.num_rows == 1 and "_count" not in none.column_names
    multi = pa.table(
        {
            GEOM: pa.array(
                [shapely.to_wkb(shapely.box(0, 0, 1, 1)), shapely.to_wkb(shapely.box(2, 2, 3, 3))], pa.binary()
            )
        }
    )
    merged = run_modifier(Aggregate, {}, multi)["output"]
    assert shapely.from_wkb(col(merged, GEOM)[0]).geom_type == "MultiPolygon"
    with pytest.raises(ConfigError, match="absents?"):
        run_modifier(Aggregate, {"group_by": ["zz"]}, multi)


# ------------------------------------------------------------------------ Value / null mappers


def test_value_mapper_unmatched_policies():
    t = table(code=["1", "2", None, "9", "2"], nom=["a", "b", "c", "d", "e"])
    maps = [{"source": "1", "target": "un"}, {"source": "2", "target": "deux"}]
    keep = run_modifier(MapValues, {"attribute": "code", "mappings": maps}, t)["output"]
    assert col(keep, "code") == ["un", "deux", None, "9", "deux"]
    numbers = run_modifier(MapValues, {"attribute": "n", "mappings": maps}, table(n=[1, 2, 3]))["output"]
    assert col(numbers, "n") == ["un", "deux", "3"]  # le nombre 1 et le texte « 1 » sont égaux
    new = run_modifier(
        MapValues,
        {"attribute": "code", "destination": "lib", "mappings": maps, "unmatched": "default", "default": "?"},
        t,
    )["output"]
    assert col(new, "lib") == ["un", "deux", "?", "?", "deux"] and col(new, "code")[3] == "9"
    nulls = run_modifier(MapValues, {"attribute": "code", "mappings": maps, "unmatched": "null"}, t)["output"]
    assert col(nulls, "code") == ["un", "deux", None, None, "deux"]
    rej = run_modifier(MapValues, {"attribute": "code", "mappings": maps, "unmatched": "reject"}, t)
    assert col(rej["output"], "nom") == ["a", "b", "e"] and col(rej["Rejected"], "nom") == ["c", "d"]


def test_value_mapper_ignore_case():
    t = table(v=["Oui", "NON", "peut-être"])
    out = run_modifier(
        MapValues,
        {
            "attribute": "v",
            "ignore_case": True,
            "mappings": [{"source": "oui", "target": "1"}, {"source": "non", "target": "0"}],
        },
        t,
    )["output"]
    assert col(out, "v") == ["1", "0", "peut-être"]


def test_null_mapper_fill_to_null_and_blank():
    t = table(n=[1, None, 3], s=["a", "  ", None], z=[-1, 5, -1])
    out = run_modifier(ReplaceNulls, {"attributes": ["n"], "value": "0"}, t)["output"]
    assert col(out, "n") == [1, 0, 3] and out.schema.field("n").type == pa.int64()
    text = run_modifier(ReplaceNulls, {"attributes": ["s"], "value": "(vide)", "blank_is_null": True}, t)["output"]
    assert col(text, "s") == ["a", "(vide)", "(vide)"]
    gone = run_modifier(ReplaceNulls, {"attributes": ["z"], "mode": "to_null", "value": "-1"}, t)["output"]
    assert col(gone, "z") == [None, 5, None]
    with pytest.raises(ConfigError, match="valide"):
        run_modifier(ReplaceNulls, {"attributes": ["n"], "value": "abc"}, t)
    everything = run_modifier(ReplaceNulls, {"value": "9"}, table(a=[None, 1], b=[None, 2]))["output"]
    assert col(everything, "a") == [9, 1] and col(everything, "b") == [9, 2]


# ------------------------------------------------------------------------ Vertex, lists, splitter


def test_vertex_creator_point_append_and_rejects():
    t = table(x=["1,5", "2", None, "abc"], y=[10.0, 20.0, 30.0, 40.0], id=[1, 2, 3, 4])
    out = run_modifier(PointFromXY, {"x": "x", "y": "y", "crs": "EPSG:2154"}, t)
    assert [shapely.from_wkb(g).x for g in col(out["output"], GEOM)] == [1.5, 2.0]
    assert get_crs(out["output"]) == "EPSG:2154" and col(out["Rejected"], "id") == [3, 4]
    lines = pa.table(
        {
            "x": [5.0, 6.0],
            "y": [5.0, 6.0],
            GEOM: pa.array([shapely.to_wkb(shapely.LineString([(0, 0), (1, 1)])), None], pa.binary()),
        }
    )
    appended = run_modifier(PointFromXY, {"x": "x", "y": "y", "mode": "append"}, lines)["output"]
    assert list(shapely.from_wkb(col(appended, GEOM)[0]).coords) == [(0, 0), (1, 1), (5, 5)]
    assert shapely.from_wkb(col(appended, GEOM)[1]).geom_type == "Point"
    z = run_modifier(PointFromXY, {"x": "x", "y": "y", "z": "z"}, table(x=[1.0], y=[2.0], z=[3.0]))["output"]
    assert shapely.from_wkb(col(z, GEOM)[0]).has_z


def test_list_exploder_rows_index_and_empty_lists():
    t = pa.table({"id": [1, 2, 3, 4], "tags": pa.array([["a", "b"], [], None, ["c"]], pa.list_(pa.string()))})
    out = run_modifier(ExplodeList, {"attribute": "tags", "element": "tag", "index": "i"}, t)["output"]
    assert col(out, "id") == [1, 1, 4] and col(out, "tag") == ["a", "b", "c"] and col(out, "i") == [0, 1, 0]
    assert "tags" not in out.column_names
    kept = run_modifier(ExplodeList, {"attribute": "tags", "keep_empty": True}, t)["output"]
    assert col(kept, "id") == [1, 1, 2, 3, 4] and col(kept, "tags") == ["a", "b", None, None, "c"]
    with pytest.raises(ConfigError, match="pas une liste"):
        run_modifier(ExplodeList, {"attribute": "id"}, t)


def test_attribute_splitter_columns_and_list():
    t = table(v=["a, b ,c", "x", None, "1,2,3,4"])
    cols = run_modifier(SplitText, {"attribute": "v", "parts": 2, "prefix": "p"}, t)["output"]
    assert col(cols, "p_1") == ["a", "x", None, "1"] and col(cols, "p_2") == ["b ,c", None, None, "2,3,4"]
    as_list = run_modifier(SplitText, {"attribute": "v", "mode": "list", "prefix": "l"}, t)["output"]
    assert col(as_list, "l") == [["a", "b", "c"], ["x"], None, ["1", "2", "3", "4"]]
    inst = SplitText(SplitText.Params(attribute="v", parts=3), None)
    assert inst.output_schema({"input": t.schema})["output"].names == ["v", "v_1", "v_2", "v_3"]
    with pytest.raises(ConfigError, match="séparateur"):
        run_modifier(SplitText, {"attribute": "v", "separator": ""}, t)


# ------------------------------------------------------------------------ Area, geometry <-> text


def test_area_calculator_planar_units_and_rejects():
    t = pa.concat_tables([polygons([(0, 0, 100, 100)], id=[1]), points([1], [1], id=[2]).select(["id", GEOM])])
    t = with_crs(t, "EPSG:2154")
    out = run_modifier(MeasureArea, {"perimeter": "_per", "area_unit": "ha"}, t)
    assert col(out["output"], "_area") == [1.0] and col(out["output"], "_per") == [400.0]
    assert col(out["Rejected"], "id") == [2]
    with pytest.raises(ConfigError, match="géographique"):
        run_modifier(MeasureArea, {}, with_crs(t, "EPSG:4326"))
    with pytest.raises(ConfigError, match="au moins"):
        run_modifier(MeasureArea, {"area": ""}, t)


def test_area_calculator_ellipsoidal():
    t = polygons([(2.0, 48.0, 3.0, 49.0)], crs="EPSG:4326", id=[1])
    out = run_modifier(MeasureArea, {"method": "ellipsoidal", "area_unit": "km2", "decimals": 0}, t)["output"]
    assert 8000 < col(out, "_area")[0] < 8400  # un degré × un degré vers 48,5° N


def test_geometry_extractor_and_replacer_roundtrip():
    t = points([1.5, 2.0], [3.0, 4.0], id=[1, 2])
    wkt = run_modifier(GeometryToAttribute, {"attribute": "wkt", "remove_geometry": True}, t)["output"]
    assert col(wkt, "wkt") == ["POINT (1.5 3)", "POINT (2 4)"] and GEOM not in wkt.column_names
    back = run_modifier(GeometryFromAttribute, {"attribute": "wkt", "crs": "EPSG:2154", "remove_attribute": True}, wkt)[
        "output"
    ]
    assert (
        shapely.from_wkb(col(back, GEOM)[1]).x == 2.0
        and get_crs(back) == "EPSG:2154"
        and "wkt" not in back.column_names
    )
    for fmt in ("geojson", "wkb_hex"):
        text = run_modifier(GeometryToAttribute, {"format": fmt}, t)["output"]
        again = run_modifier(GeometryFromAttribute, {"attribute": "geometry_text", "format": fmt}, text)["output"]
        assert shapely.from_wkb(col(again, GEOM)[0]).x == 1.5
    rounded = run_modifier(GeometryToAttribute, {"precision": 0}, points([1.6], [2.4]))["output"]
    assert col(rounded, "geometry_text") == ["POINT (2 2)"]


def test_geometry_replacer_bad_and_empty_values():
    t = table(g=["POINT (1 2)", "", None, "POINT (oups"], id=[1, 2, 3, 4])
    out = run_modifier(GeometryFromAttribute, {"attribute": "g"}, t)
    assert col(out["output"], "id") == [1, 2, 3] and col(out["output"], GEOM)[1] is None
    assert col(out["Rejected"], "id") == [4] and "illisible" in col(out["Rejected"], "_rejection_reason")[0]
    with pytest.raises(ConfigError, match="n'a pas|absent|non renseigné"):
        run_modifier(GeometryFromAttribute, {}, t)
    with pytest.raises(ConfigError, match="réservé"):
        run_modifier(GeometryToAttribute, {"attribute": "geometry"}, points([1], [1]))
