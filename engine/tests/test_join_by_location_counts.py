"""JoinByLocation : comptes et sommes (ex-CountPointsInPolygon), quel que soit le côté gardé en mémoire."""

import pyarrow as pa
import pytest
import shapely
from etl_engine.data import GEOM, with_crs
from etl_engine.modifiers.join_by_location import JoinByLocation
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
COUNT = {"count_attribute": "_count"}


@pytest.fixture(params=["base", "related"])
def indexed(request, monkeypatch):
    """Les deux stratégies : « base » indexée (entités liées lues par lots) ou « related » indexée."""
    if request.param == "related":
        monkeypatch.setattr(JoinByLocation, "run", JoinByLocation._index_related)
    return request.param


def count(params, base=AREAS, related=POINTS, **kw):
    return run_modifier(JoinByLocation, {**COUNT, **params}, {"base": base, "related": related}, **kw)


def test_counts_every_area_including_empty_ones_and_sums_attributes(indexed):
    out = count({"sum_attributes": ["pop"]})["output"]
    assert col(out, "code") == ["A", "B", "C"]  # ordre et attributs des polygones conservés
    assert col(out, "_count") == [3, 2, 0]  # (10, 5) est sur la limite commune : il compte dans les deux
    assert col(out, "pop_sum") == [35.0, 5.0, 0.0]  # pop absente : 0
    assert out.schema.metadata == AREAS.schema.metadata  # SCR conservé


def test_first_counts_a_boundary_point_once_and_counts_are_renamed(indexed):
    out = count({"on_multiple": "first", "count_attribute": "nb"})["output"]
    assert col(out, "nb") == [3, 1, 0] and "_count" not in out.column_names


def test_related_features_arrive_in_many_batches_counted_in_parallel(indexed):
    many = make([shapely.Point(i % 20, 5) for i in range(5000)])
    out = count({"on_multiple": "first"}, related=many, batch_rows=300, ctx=Context(workers=4))["output"]
    assert sum(col(out, "_count")) == 5000


def test_first_spans_batches_of_base_features(indexed):
    """« first » : une entité liée à deux entités base de lots différents ne compte que dans la première."""
    out = count({"on_multiple": "first"}, batch_rows=1, ctx=Context(workers=3))["output"]
    assert col(out, "_count") == [3, 1, 0]


def test_any_geometry_counts_where_it_touches_and_areas_without_geometry_are_rejected(indexed):
    lines = make([shapely.LineString([(5, 5), (15, 5)])])
    areas = make([shapely.box(0, 0, 10, 10), None, shapely.box(10, 0, 20, 10)], code=["A", "X", "B"])
    out = count({}, base=areas, related=lines)
    assert col(out["output"], "_count") == [1, 1] and col(out["Rejected"], "code") == ["X"]


def test_relations_are_read_from_base_to_related(indexed):
    """base <relation> related, quel que soit le côté indexé (contains / within inversés au besoin)."""
    small = make([shapely.box(1, 1, 2, 2)])
    assert col(count({"predicate": "contains"}, related=small)["output"], "_count") == [1, 0, 0]
    assert col(count({"predicate": "within"}, related=small)["output"], "_count") == [0, 0, 0]
    assert col(count({"predicate": "within"}, base=small, related=AREAS)["output"], "_count") == [1]
    assert col(count({"predicate": "covered_by"}, base=small, related=AREAS)["output"], "_count") == [1]


def test_errors_and_schema():
    with pytest.raises(ConfigError, match="absents de « related »"):
        count({"sum_attributes": ["nope"]})
    with pytest.raises(ConfigError, match="pas numérique"):
        count({"sum_attributes": ["code"]}, related=AREAS)
    with pytest.raises(ConfigError):
        count({}, related=make([shapely.Point(1, 1)], crs="EPSG:4326"))
    schema = JoinByLocation(JoinByLocation.Params(sum_attributes=["pop"], **COUNT), Context()).output_schema(
        {"base": AREAS.schema, "related": POINTS.schema}
    )["output"]
    assert schema.field("_count").type == pa.int64() and schema.field("pop_sum").type == pa.float64()
