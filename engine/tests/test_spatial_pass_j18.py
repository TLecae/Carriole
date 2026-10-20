"""Jalon 18, passe sur les modificateurs spatiaux : calculs vectorisés, mêmes résultats qu'entité par entité."""

import numpy as np
import pyarrow as pa
import pytest
import shapely
from etl_engine.data import GEOM, replace_geometries, with_crs
from etl_engine.modifiers.aggregate import Aggregate, collect_groups
from etl_engine.modifiers.check_validity import CheckValidity
from etl_engine.modifiers.dissolve import Dissolve
from etl_engine.modifiers.extract_coordinates import ExtractCoordinates
from etl_engine.modifiers.geometry_from_attribute import GeometryFromAttribute
from etl_engine.modifiers.multipart_to_singleparts import MultipartToSingleparts
from etl_engine.modifiers.point_from_xy import PointFromXY
from etl_engine.modifiers.split_by_geometry_type import SplitByGeometryType
from etl_engine.sdk import Context
from etl_engine.testing import run_modifier


def shapes(geoms, **columns):
    table = pa.table(columns) if columns else pa.table({"_n": list(range(len(geoms)))})
    wkb = [None if g is None else shapely.to_wkb(g) for g in geoms]
    return with_crs(table.append_column(GEOM, pa.array(wkb, pa.binary())), "EPSG:2154")


def geoms_of(table):
    return list(shapely.from_wkb(table.column(GEOM).to_numpy(zero_copy_only=False)))


def test_replace_geometries_reencodes_only_the_changed_rows():
    t = shapes([shapely.box(0, 0, 1, 1), shapely.Point(5, 5)])
    raw = t.column(GEOM).to_pylist()
    new = np.array([shapely.Point(9, 9), shapely.Point(7, 7)], dtype=object)
    out = replace_geometries(t, new, np.array([False, True]))
    assert out.column(GEOM)[0].as_py() == raw[0]  # octet pour octet
    assert shapely.from_wkb(out.column(GEOM)[1].as_py()).equals(shapely.Point(7, 7))


def test_deaggregator_parts_ranks_counts_and_nested_collections():
    nested = shapely.GeometryCollection([shapely.MultiPoint([(0, 0), (1, 1)]), shapely.LineString([(0, 0), (2, 2)])])
    t = shapes([shapely.MultiPolygon([shapely.box(0, 0, 1, 1), shapely.box(2, 2, 3, 3)]), shapely.Point(5, 5), None,
                shapely.Polygon(), nested])  # fmt: skip
    out = run_modifier(MultipartToSingleparts, {"count_attribute": "_n_parts"}, t, batch_rows=2)["output"]
    assert out.column("_n").to_pylist() == [0, 0, 1, 2, 3, 4, 4, 4]
    assert out.column("_part").to_pylist() == [0, 1, 0, 0, 0, 0, 1, 2]
    assert out.column("_n_parts").to_pylist() == [2, 2, 1, 1, 1, 3, 3, 3]
    kinds = [None if g is None else g.geom_type for g in geoms_of(out)]
    assert kinds == ["Polygon", "Polygon", "Point", None, "Polygon", "Point", "Point", "LineString"]
    assert out.column(GEOM)[2].as_py() == t.column(GEOM)[1].as_py()  # une géométrie simple passe telle quelle
    flat = run_modifier(MultipartToSingleparts, {"recursive": False}, t)["output"]
    assert [g.geom_type for g in geoms_of(flat)[-2:]] == ["MultiPoint", "LineString"]


def test_aggregate_collects_by_family_and_mixed_groups():
    geoms = np.array(
        [shapely.box(0, 0, 1, 1), shapely.Point(0, 0), shapely.MultiPolygon([shapely.box(2, 2, 3, 3)]),
         shapely.LineString([(0, 0), (1, 1)]), None, shapely.Point(1, 1)],
        dtype=object,
    )  # fmt: skip
    groups = np.array([0, 1, 0, 2, 3, 2])
    out = collect_groups(geoms, groups, 4)
    assert out[0].geom_type == "MultiPolygon" and shapely.get_num_geometries(out[0]) == 2
    assert out[1].geom_type == "MultiPoint"
    assert out[2].geom_type == "GeometryCollection" and shapely.get_num_geometries(out[2]) == 2
    assert out[3] is None
    t = shapes(list(geoms), g=["a", "b", "a", "c", "d", "c"])
    agg = run_modifier(Aggregate, {"group_by": ["g"]}, t)["output"]
    assert agg.column("g").to_pylist() == ["a", "b", "c", "d"] and agg.column("_count").to_pylist() == [2, 1, 2, 1]


def test_coordinate_extractor_start_and_end_vertices():
    t = shapes([shapely.LineString([(0, 0), (5, 1), (9, 2)]), shapely.box(0, 0, 1, 1), None, shapely.Point(3, 4)])
    start = run_modifier(ExtractCoordinates, {"source": "start"}, t)["output"]
    end = run_modifier(ExtractCoordinates, {"source": "end"}, t)["output"]
    assert start.column("x").to_pylist()[:2] == [0.0, 1.0] and end.column("x").to_pylist()[0] == 9.0
    assert start.column("x").to_pylist()[2] is None and end.column("y").to_pylist()[3] == 4.0


def test_vertex_creator_appends_to_lines_and_points():
    t = shapes([shapely.LineString([(0, 0), (1, 1)]), shapely.Point(5, 5), None], x=[2.0, 6.0, 7.0], y=[2.0, 6.0, 7.0])
    out = run_modifier(PointFromXY, {"x": "x", "y": "y", "mode": "append"}, t)["output"]
    a, b, c = geoms_of(out)
    assert list(a.coords) == [(0, 0), (1, 1), (2, 2)] and list(b.coords) == [(5, 5), (6, 6)]
    assert c.equals(shapely.Point(7, 7))


def test_geometry_replacer_reads_in_bulk_and_explains_bad_values():
    t = pa.table({"txt": ["POINT (1 2)", " ", None, "POINT (oops)", "LINESTRING (0 0, 1 1)"]})
    out = run_modifier(GeometryFromAttribute, {"attribute": "txt", "crs": "EPSG:2154"}, t)
    assert out["output"].num_rows == 4 and geoms_of(out["output"])[0].equals(shapely.Point(1, 2))
    assert geoms_of(out["output"])[1] is None and geoms_of(out["output"])[3].geom_type == "LineString"
    reason = out["Rejected"].column("_rejection_reason").to_pylist()
    assert len(reason) == 1 and reason[0].startswith("géométrie illisible (wkt)")


def test_geometry_filter_families_and_validator_keeps_valid_bytes():
    t = shapes([shapely.Point(0, 0), shapely.MultiLineString([[(0, 0), (1, 1)]]), shapely.box(0, 0, 1, 1), None,
                shapely.GeometryCollection([shapely.Point(0, 0)]), shapely.Polygon()])  # fmt: skip
    out = run_modifier(SplitByGeometryType, {}, t)
    assert {k: v.column("_n").to_pylist() for k, v in out.items()} == {
        "Point": [0], "Line": [1], "Area": [2], "Collection": [4], "NoGeometry": [3, 5],
    }  # fmt: skip
    bowtie = shapely.Polygon([(0, 0), (2, 2), (2, 0), (0, 2), (0, 0)])
    v = shapes([shapely.box(0, 0, 1, 1), bowtie])
    valid = run_modifier(CheckValidity, {"repair": True}, v)["Valid"]
    assert valid.column(GEOM)[0].as_py() == v.column(GEOM)[0].as_py()  # valide : WKB d'origine
    assert shapely.is_valid(geoms_of(valid)[1]) and valid.column("_repaired_reason").to_pylist()[0] is None


@pytest.mark.parametrize("touching", [False, True])
def test_dissolve_by_disjoint_subsets_equals_a_plain_union(touching):
    rng = np.random.default_rng(2)
    x, y = rng.uniform(0, 1000, 3000), rng.uniform(0, 1000, 3000)
    size = 30.0 if touching else 3.0  # amas de carrés qui se touchent, ou presque tous isolés
    cells = shapely.box(x, y, x + size, y + size)
    t = shapes(list(cells), g=["a"] * 3000)
    out = run_modifier(Dissolve, {}, t, batch_rows=500, ctx=Context(workers=4))["output"]
    merged = geoms_of(out)[0]
    assert merged.area == pytest.approx(shapely.union_all(cells).area)
    assert out.column("_count").to_pylist() == [3000]


def test_dissolve_never_decodes_more_than_a_chunk_at_once(monkeypatch):
    """Régression (1,47 M de parcelles : 6 Go) : les géométries restent en WKB, décodées par morceaux seulement."""
    from etl_engine.modifiers import dissolve

    monkeypatch.setattr(dissolve, "CHUNK", 200)
    monkeypatch.setattr(dissolve, "BOUNDS_ROWS", 300)
    seen = []
    real = dissolve._decode
    monkeypatch.setattr(dissolve, "_decode", lambda wkb, rows: (seen.append(len(rows)), real(wkb, rows))[1])
    rng = np.random.default_rng(4)
    x, y = rng.uniform(0, 1000, 3000), rng.uniform(0, 1000, 3000)
    cells = shapely.box(x, y, x + 12, y + 12)
    groups = ["big"] * 2200 + ["small"] * 700 + ["tiny"] * 100
    t = shapes(list(cells), g=groups)
    out = run_modifier(Dissolve, {"group_by": ["g"]}, t, batch_rows=500, ctx=Context(workers=4))["output"]
    assert out.column("g").to_pylist() == ["big", "small", "tiny"] and out.column("_count").to_pylist() == [
        2200,
        700,
        100,
    ]
    for name, lo, hi in (("big", 0, 2200), ("small", 2200, 2900), ("tiny", 2900, 3000)):
        assert geoms_of(out)[["big", "small", "tiny"].index(name)].area == pytest.approx(
            shapely.union_all(cells[lo:hi]).area
        )
    assert max(seen) <= 300  # jamais le groupe entier (2 200 lignes), ni le port entier (3 000)
