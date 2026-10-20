"""CountPointsInPolygon : nombre de points (et sommes) dans chaque polygone."""

import pyarrow as pa
import pytest
import shapely
from etl_engine.data import GEOM, with_crs
from etl_engine.modifiers.count_points_in_polygon import CountPointsInPolygon
from etl_engine.sdk import ConfigError, Context
from etl_engine.testing import run_modifier


def make(geoms, crs="EPSG:2154", **columns):
    table = pa.table(columns) if columns else pa.table({"_n": list(range(len(geoms)))})
    wkb = [None if g is None else shapely.to_wkb(g) for g in geoms]
    return with_crs(table.append_column(GEOM, pa.array(wkb, pa.binary())), crs)


def col(table, name):
    return table.column(name).to_pylist()


AREAS = make(
    [shapely.box(0, 0, 10, 10), shapely.box(10, 0, 20, 10), shapely.box(100, 100, 110, 110)], code=["A", "B", "C"]
)
POINTS = make(
    [shapely.Point(1, 1), shapely.Point(2, 2), shapely.Point(10, 5), shapely.Point(15, 5), shapely.Point(50, 50), None],
    pop=[10, 20, 5, None, 7, 3],
)


def test_counts_every_area_including_empty_ones_and_sums_attributes():
    out = run_modifier(CountPointsInPolygon, {"sum_attributes": ["pop"]}, {"areas": AREAS, "points": POINTS})["output"]
    assert col(out, "code") == ["A", "B", "C"]  # ordre et attributs des polygones conservés
    assert col(out, "_count") == [3, 2, 0]  # (10, 5) est sur la limite commune : il compte dans les deux
    assert col(out, "pop_sum") == [35.0, 5.0, 0.0]  # pop absente : 0
    assert out.schema.metadata == AREAS.schema.metadata  # SCR conservé


def test_first_counts_a_boundary_point_once_and_counts_are_renamed():
    params = {"on_multiple": "first", "count_attribute": "nb"}
    out = run_modifier(CountPointsInPolygon, params, {"areas": AREAS, "points": POINTS})["output"]
    assert col(out, "nb") == [3, 1, 0] and "_count" not in out.column_names


def test_points_arrive_in_many_batches_counted_in_parallel():
    many = make([shapely.Point(i % 20, 5) for i in range(5000)])
    out = run_modifier(
        CountPointsInPolygon,
        {"on_multiple": "first"},
        {"areas": AREAS, "points": many},
        batch_rows=300,
        ctx=Context(workers=4),
    )["output"]
    assert sum(col(out, "_count")) == 5000


def test_any_geometry_counts_where_it_touches_and_areas_without_geometry_are_rejected():
    lines = make([shapely.LineString([(5, 5), (15, 5)])])
    areas = make([shapely.box(0, 0, 10, 10), None, shapely.box(10, 0, 20, 10)], code=["A", "X", "B"])
    out = run_modifier(CountPointsInPolygon, {}, {"areas": areas, "points": lines})
    assert col(out["output"], "_count") == [1, 1] and col(out["Rejected"], "code") == ["X"]


def test_errors_and_schema():
    with pytest.raises(ConfigError, match="absents des points"):
        run_modifier(CountPointsInPolygon, {"sum_attributes": ["nope"]}, {"areas": AREAS, "points": POINTS})
    with pytest.raises(ConfigError, match="pas numérique"):
        run_modifier(CountPointsInPolygon, {"sum_attributes": ["code"]}, {"areas": AREAS, "points": AREAS})
    with pytest.raises(ConfigError):
        run_modifier(CountPointsInPolygon, {}, {"areas": AREAS, "points": make([shapely.Point(1, 1)], crs="EPSG:4326")})
    schema = CountPointsInPolygon(CountPointsInPolygon.Params(sum_attributes=["pop"]), Context()).output_schema(
        {"areas": AREAS.schema, "points": POINTS.schema}
    )["output"]
    assert schema.field("_count").type == pa.int64() and schema.field("pop_sum").type == pa.float64()
