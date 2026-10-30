"""JoinByLocation : attributs récupérés des entités liées, comme une jointure attributaire (première entité liée, une
entité par entité liée, ou liste des valeurs)."""

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely
from etl_engine.analysis import analyze
from etl_engine.data import GEOM, get_crs, with_crs
from etl_engine.modifiers.join_by_location import JoinByLocation
from etl_engine.sdk import ConfigError, Context
from etl_engine.testing import run_modifier

from conftest import make_flow


def make(geoms, **columns):
    wkb = [None if g is None else shapely.to_wkb(g) for g in geoms]
    return with_crs(pa.table(columns).append_column(GEOM, pa.array(wkb, pa.binary())), "EPSG:2154")


def col(table, name):
    return table.column(name).to_pylist()


# parcelles (base) : P1 dans la commune A, P2 sur la limite A | B, P3 hors de tout, P4 sans géométrie
PARCELS = make(
    [shapely.box(1, 1, 2, 2), shapely.box(9, 1, 11, 2), shapely.box(50, 50, 51, 51), None],
    id=["P1", "P2", "P3", "P4"],
    code=["p1", "p2", "p3", "p4"],
)
TOWNS = make(
    [shapely.box(0, 0, 10, 10), shapely.box(10, 0, 20, 10)],
    code=["A", "B"],
    nom=["Aville", "Bville"],
    pop=pa.array([100, 200], pa.int64()),
)


def relate(params, base=PARCELS, related=TOWNS, **kw):
    return run_modifier(JoinByLocation, params, {"base": base, "related": related}, **kw)


def test_first_related_feature_gives_its_attributes_and_none_without_any():
    out = relate({"attributes": ["nom", "pop"], "count_attribute": ""})
    assert col(out["output"], "id") == ["P1", "P2", "P3"]  # ordre de « base » ; P4 (sans géométrie) rejetée
    assert col(out["output"], "nom") == ["Aville", "Aville", None]  # P2 touche A et B : la première, A
    assert out["output"].schema.field("pop").type == pa.int64()  # type d'origine
    assert get_crs(out["output"]) == "EPSG:2154" and col(out["Rejected"], "id") == ["P4"]


def test_all_gives_one_feature_per_related_feature_in_order():
    out = relate({"attributes": ["nom"], "merge": "all", "prefix": "commune_"}, batch_rows=2)["output"]
    assert col(out, "id") == ["P1", "P2", "P2", "P3"]
    assert col(out, "commune_nom") == ["Aville", "Aville", "Bville", None]
    assert col(out, "_related_count") == [1, 2, 2, 0]


def test_list_joins_the_distinct_values_of_each_attribute():
    out = relate({"attributes": ["nom", "pop"], "merge": "list", "separator": " | "})["output"]
    assert col(out, "nom") == ["Aville", "Aville | Bville", None]
    assert col(out, "pop") == ["100", "100 | 200", None]


def test_name_conflicts_follow_the_attribute_join():
    """« code » existe des deux côtés : la valeur de « related » (other), ou celle de « base » gardée."""
    assert col(relate({"attributes": ["code"]})["output"], "code") == ["A", "A", None]
    assert col(relate({"attributes": ["code"], "conflict": "base"})["output"], "code") == ["p1", "p2", "p3"]
    assert col(relate({"attributes": ["code"], "prefix": "t_"})["output"], "t_code") == ["A", "A", None]


def test_parallel_batches_of_base_keep_their_order():
    out = relate({"attributes": ["nom"], "merge": "all"}, batch_rows=1, ctx=Context(workers=3))["output"]
    assert col(out, "id") == ["P1", "P2", "P2", "P3"]


def test_attributes_of_related_are_checked_against_related_not_base(registry, tmp_path):
    """L'éditeur ne propose que les attributs de « related » (`x-etl-input`) ; l'analyse ne les cherche pas dans
    « base » (avant : « attribut absent en amont » pour un attribut des entités liées)."""
    pq.write_table(PARCELS, tmp_path / "parcels.parquet")
    pq.write_table(TOWNS, tmp_path / "towns.parquet")
    nodes = [
        {"id": "p", "type": "Reader", "params": {"path": str(tmp_path / "parcels.parquet")}},
        {"id": "t", "type": "Reader", "params": {"path": str(tmp_path / "towns.parquet")}},
        {"id": "j", "type": "JoinByLocation", "params": {"attributes": ["nom"], "sum_attributes": ["pop"]}},
    ]
    flow = make_flow(nodes, [("p", "output", "j", "base"), ("t", "output", "j", "related")])
    report = analyze(flow, registry)
    assert not [i for i in report["issues"] if i["code"] == "missing_attribute"]
    assert "nom" in [f["name"] for f in report["schemas"]["j/output"]["fields"]]
    field = JoinByLocation.Params.model_json_schema()["properties"]["attributes"]
    assert field["x-etl-widget"] == "attributes" and field["x-etl-input"] == "related"


def test_schema_and_errors():
    params = JoinByLocation.Params(attributes=["nom", "pop"], prefix="t_")
    schema = JoinByLocation(params, Context()).output_schema({"base": PARCELS.schema, "related": TOWNS.schema})
    assert schema["output"].field("t_nom").type == pa.string() and schema["output"].field("t_pop").type == pa.int64()
    assert JoinByLocation(params, Context()).output_schema({"base": PARCELS.schema, "related": None}) == {
        "output": None
    }
    with pytest.raises(ConfigError, match="absents de « related »"):
        relate({"attributes": ["nope"]})
    with pytest.raises(ConfigError, match="géométrie"):
        relate({"attributes": [GEOM]})
    with pytest.raises(ConfigError, match="attribut calculé"):
        relate({"attributes": ["count"], "count_attribute": "count"}, related=make([shapely.Point(1, 1)], count=[1]))
