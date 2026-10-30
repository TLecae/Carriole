"""Catalogue, lot A : statistiques, jointures, tri, doublons, compteur, retrait d'attributs, reprojection."""

import pyarrow as pa
import pytest
import shapely
from etl_engine.data import GEOM, get_crs
from etl_engine.modifiers.counter import Counter
from etl_engine.modifiers.drop_attributes import DropAttributes
from etl_engine.modifiers.find_duplicates import FindDuplicates
from etl_engine.modifiers.join_by_field import JoinByField
from etl_engine.modifiers.order_by import OrderBy
from etl_engine.modifiers.reproject import Reproject
from etl_engine.modifiers.statistics_by_group import StatisticsByGroup
from etl_engine.sdk import ConfigError
from etl_engine.testing import points, run_modifier, table


def col(t, name):
    return t.column(name).to_pylist()


# ------------------------------------------------------------------------ StatisticsByGroup


@pytest.fixture
def communes():
    return table(
        dep=["31", "31", "09", "09", "09", None],
        pop=[100, 300, 50, None, 150, 10],
        nom=["a", "b", "c", "d", "e", "f"],
    )


def test_statistics_per_group_attached_and_summary(communes):
    out = run_modifier(
        StatisticsByGroup,
        {"group_by": ["dep"], "attributes": ["pop"], "functions": ["count", "sum", "mean", "min", "max"]},
        communes,
    )
    summary = {r["dep"]: r for r in out["summary"].to_pylist()}
    assert summary["31"]["pop_sum"] == 400 and summary["31"]["pop_mean"] == 200 and summary["31"]["_count"] == 2
    assert summary["09"]["pop_count"] == 2 and summary["09"]["pop_sum"] == 200  # la valeur vide est ignorée
    assert summary["09"]["_count"] == 3 and summary[None]["pop_sum"] == 10  # le groupe « sans département » existe
    # chaque entité porte les statistiques de son groupe, dans l'ordre d'origine
    assert col(out["output"], "nom") == list("abcdef")
    assert col(out["output"], "pop_sum") == [400, 400, 200, 200, 200, 10]


def test_statistics_without_group_median_stddev_distinct_mode():
    t = table(v=[1, 2, 2, 3, 100], s=["x", "y", "y", "y", "z"])
    out = run_modifier(
        StatisticsByGroup,
        {"attributes": ["v", "s"], "functions": ["median", "stddev", "distinct", "mode"], "attach": False},
        t,
    )
    row = out["summary"].to_pylist()[0]
    assert row["v_median"] == 2 and row["v_distinct"] == 4 and row["s_mode"] == "y" and row["s_distinct"] == 3
    assert row["v_stddev"] == pytest.approx(43.5, abs=0.5)
    assert out["output"].num_rows == 5 and "v_median" not in out["output"].column_names


def test_statistics_text_numbers_and_geometry_crs_kept():
    t = points([0, 1], [0, 1], v=["10", "x"], g=["a", "a"])
    out = run_modifier(StatisticsByGroup, {"group_by": ["g"], "attributes": ["v"], "functions": ["sum"]}, t)
    assert col(out["summary"], "v_sum") == [10]  # « x » n'est pas un nombre : ignoré
    assert GEOM in out["output"].column_names and get_crs(out["output"]) == "EPSG:2154"


def test_statistics_config_errors(communes):
    with pytest.raises(ConfigError, match="absents?"):
        run_modifier(StatisticsByGroup, {"attributes": ["nope"]}, communes)
    with pytest.raises(ConfigError, match="écraseraient"):
        run_modifier(
            StatisticsByGroup,
            {"attributes": ["pop"], "functions": ["sum"]},
            communes.append_column("pop_sum", pa.array([0] * 6)),
        )
    with pytest.raises(ConfigError, match="rien à calculer"):
        run_modifier(StatisticsByGroup, {"count_rows": False}, communes)


def test_statistics_output_schema_is_known_without_data(communes):
    inst = StatisticsByGroup(StatisticsByGroup.Params(attributes=["pop"], functions=["sum"]), None)
    schemas = inst.output_schema({"input": communes.schema})
    assert schemas["output"].names == [*communes.column_names, "_count", "pop_sum"]
    assert schemas["summary"].names == ["_count", "pop_sum"]


# ------------------------------------------------------------------------ JoinByField / Merger


@pytest.fixture
def base():
    return points([0, 1, 2, 3], [0, 0, 0, 0], code=["A", "B", "C", None], v=[1, 2, 3, 4])


@pytest.fixture
def other():
    return table(code=["A", "B", "B", "Z"], label=["alpha", "bravo", "bravo2", "zulu"], v=[10, 20, 21, 30])


def test_joiner_ports_and_attributes(base, other):
    out = run_modifier(
        JoinByField,
        {"base_keys": ["code"], "join_keys": ["code"], "attributes": ["label"], "on_multiple": "first"},
        {"base": base, "join": other},
    )
    assert col(out["Joined"], "code") == ["A", "B"] and col(out["Joined"], "label") == ["alpha", "bravo"]
    assert col(out["UnjoinedBase"], "code") == ["C", None]  # une clé nulle ne s'apparie jamais
    assert col(out["UnjoinedJoin"], "code") == ["Z"]  # seul « zulu » ne correspond à aucune entité de base
    assert GEOM in out["Joined"].column_names and get_crs(out["Joined"]) == "EPSG:2154"


def test_joiner_all_matches_conflict_and_prefix(base, other):
    out = run_modifier(
        JoinByField, {"base_keys": ["code"], "join_keys": ["code"], "prefix": "j_"}, {"base": base, "join": other}
    )
    assert col(out["Joined"], "j_label") == ["alpha", "bravo", "bravo2"]  # B correspond deux fois
    assert col(out["Joined"], "v") == [1, 2, 2] and col(out["Joined"], "j_v") == [10, 20, 21]
    replaced = run_modifier(
        JoinByField,
        {"base_keys": ["code"], "join_keys": ["code"], "on_multiple": "first"},
        {"base": base, "join": other},
    )
    assert col(replaced["Joined"], "v") == [10, 20]  # par défaut la valeur de « join » remplace
    kept = run_modifier(
        JoinByField,
        {"base_keys": ["code"], "join_keys": ["code"], "on_multiple": "first", "conflict": "base"},
        {"base": base, "join": other},
    )
    assert col(kept["Joined"], "v") == [1, 2]


def test_joiner_left_join_and_errors(base, other):
    out = run_modifier(
        JoinByField,
        {
            "base_keys": ["code"],
            "join_keys": ["code"],
            "attributes": ["label"],
            "on_multiple": "first",
            "keep_unjoined": True,
        },
        {"base": base, "join": other},
    )
    assert col(out["Joined"], "label") == ["alpha", "bravo", None, None] and out["UnjoinedBase"].num_rows == 0
    with pytest.raises(ConfigError, match="plusieurs correspondances"):
        run_modifier(
            JoinByField,
            {"base_keys": ["code"], "join_keys": ["code"], "on_multiple": "error"},
            {"base": base, "join": other},
        )
    with pytest.raises(ConfigError, match="autant de clés"):
        run_modifier(JoinByField, {"base_keys": ["code"]}, {"base": base, "join": other})
    with pytest.raises(ConfigError, match="absentes"):
        run_modifier(JoinByField, {"base_keys": ["code"], "join_keys": ["nope"]}, {"base": base, "join": other})


def test_joiner_keys_of_different_types_compare_as_text():
    out = run_modifier(
        JoinByField,
        {"base_keys": ["id"], "join_keys": ["id"]},
        {"base": table(id=[1, 2], a=["x", "y"]), "join": table(id=["2", "3"], b=["u", "v"])},
    )
    assert col(out["Joined"], "a") == ["y"] and col(out["Joined"], "b") == ["u"]


def test_joiner_empty_join_side(base):
    out = run_modifier(
        JoinByField,
        {"base_keys": ["code"], "join_keys": ["code"]},
        {"base": base, "join": table(code=pa.array([], pa.string()))},
    )
    assert out["Joined"].num_rows == 0 and out["UnjoinedBase"].num_rows == 4


def test_joiner_geometry_choices_and_crs(base):
    """Géométrie du résultat : celle de « base », celle de « join » (avec son système) ou aucune (ex-LookupJoin)."""
    other = points([10, 11], [10, 11], crs="EPSG:4326", code=["A", "B"], nom=["x", "y"])
    params = {"base_keys": ["code"], "join_keys": ["code"]}
    kept = run_modifier(JoinByField, params, {"base": base, "join": other})
    assert col(kept["Joined"], "nom") == ["x", "y"] and get_crs(kept["Joined"]) == get_crs(base)
    assert shapely.from_wkb(col(kept["Joined"], GEOM)[0]).x == 0  # géométrie de « base »
    taken = run_modifier(JoinByField, {**params, "geometry": "join"}, {"base": base, "join": other})
    assert shapely.from_wkb(col(taken["Joined"], GEOM)[0]).x == 10 and get_crs(taken["Joined"]) == "EPSG:4326"
    none = run_modifier(JoinByField, {**params, "geometry": "none"}, {"base": base, "join": other})
    assert GEOM not in none["Joined"].column_names
    assert col(kept["UnjoinedBase"], "code") == ["C", None]
    left = run_modifier(
        JoinByField, {**params, "geometry": "join", "keep_unjoined": True}, {"base": base, "join": other}
    )
    assert col(left["Joined"], GEOM)[2:] == [None, None]  # gardées sans correspondance : pas de géométrie de « join »


# ------------------------------------------------------------------------ OrderBy, Counter, Remover


def test_sorter_multi_keys_nulls_last_and_stability():
    t = table(a=["b", "a", None, "a", "b"], n=["10", "9", "1", "100", None], i=[0, 1, 2, 3, 4])
    out = run_modifier(
        OrderBy, {"keys": [{"attribute": "a"}, {"attribute": "n", "kind": "number", "order": "descending"}]}, t
    )["output"]
    assert col(out, "i") == [3, 1, 0, 4, 2]
    stable = run_modifier(OrderBy, {"keys": [{"attribute": "a"}]}, t)["output"]
    assert col(stable, "i") == [1, 3, 0, 4, 2]  # à égalité, l'ordre d'arrivée
    with pytest.raises(ConfigError, match="tri impossible.*convert"):
        run_modifier(OrderBy, {"keys": [{"attribute": "a", "kind": "number"}]}, t)
    with pytest.raises(ConfigError, match="clé de tri"):
        run_modifier(OrderBy, {}, t)


def test_sorter_keeps_geometry_and_crs():
    t = points([3, 1, 2], [0, 0, 0], k=[3, 1, 2])
    out = run_modifier(OrderBy, {"keys": [{"attribute": "k"}]}, t)["output"]
    assert [shapely.from_wkb(g).x for g in col(out, GEOM)] == [1, 2, 3] and get_crs(out) == "EPSG:2154"


def test_counter_across_batches_and_groups():
    t = table(g=["a", "b", "a", "a", "b", "c", "a"])
    plain = run_modifier(Counter, {"start": 10, "step": 5}, t)["output"]  # lots de 3 : l'état se poursuit
    assert col(plain, "_count") == [10, 15, 20, 25, 30, 35, 40]
    grouped = run_modifier(Counter, {"group_by": ["g"], "attribute": "rang"}, t)["output"]
    assert col(grouped, "rang") == [1, 1, 2, 3, 2, 1, 4]


def test_attribute_remover():
    t = points([0], [0], a=[1], b=[2])
    out = run_modifier(DropAttributes, {"attributes": ["a"]}, t)["output"]
    assert out.column_names == ["b", GEOM] and get_crs(out) == "EPSG:2154"
    assert (
        GEOM
        not in run_modifier(DropAttributes, {"attributes": ["a"], "remove_geometry": True}, t)["output"].column_names
    )
    with pytest.raises(ConfigError, match="absents?"):
        run_modifier(DropAttributes, {"attributes": ["zz"]}, t)
    assert run_modifier(DropAttributes, {"attributes": ["zz"], "ignore_missing": True}, t)["output"].num_columns == 3


# ------------------------------------------------------------------------ FindDuplicates


def test_duplicate_filter_across_batches():
    t = table(k=["a", "b", "a", None, None, "b"], v=[1, 2, 3, 4, 5, 6])
    out = run_modifier(FindDuplicates, {"keys": ["k"]}, t)
    assert col(out["Unique"], "v") == [1, 2, 4]
    assert col(out["Duplicate"], "v") == [3, 5, 6]
    everything = run_modifier(FindDuplicates, {}, table(a=[1, 1, 2], b=["x", "x", "x"]))
    assert col(everything["Duplicate"], "a") == [1]


# ------------------------------------------------------------------------ Reproject


def test_reprojector_lambert93_to_wgs84_and_back():
    t = points([652000.0], [6862000.0], crs="EPSG:2154")  # Paris (approx.)
    out = run_modifier(Reproject, {"target": "EPSG:4326"}, t)["output"]
    p = shapely.from_wkb(col(out, GEOM)[0])
    assert get_crs(out) == "EPSG:4326" and 2.3 < p.x < 2.4 and 48.8 < p.y < 48.9
    back = run_modifier(Reproject, {"target": "EPSG:2154"}, out)["output"]
    q = shapely.from_wkb(col(back, GEOM)[0])
    assert abs(q.x - 652000.0) < 0.01 and abs(q.y - 6862000.0) < 0.01


def test_reprojector_forced_source_nulls_z_and_same_crs():
    t = points([2.35], [48.85], crs=None)
    with pytest.raises(ConfigError, match="n'ont pas de système"):
        run_modifier(Reproject, {"target": "EPSG:2154"}, t)
    out = run_modifier(Reproject, {"target": "EPSG:2154", "source": "EPSG:4326"}, t)["output"]
    assert 650000 < shapely.from_wkb(col(out, GEOM)[0]).x < 660000
    mixed = pa.table(
        {"g": pa.array([shapely.to_wkb(shapely.Point(2.35, 48.85, 30.0)), None], pa.binary())}
    ).rename_columns([GEOM])
    z = run_modifier(Reproject, {"target": "EPSG:2154", "source": "EPSG:4326"}, mixed)["output"]
    assert shapely.from_wkb(col(z, GEOM)[0]).has_z and col(z, GEOM)[1] is None
    same = run_modifier(Reproject, {"target": "EPSG:2154"}, points([1], [1]))["output"]
    assert shapely.from_wkb(col(same, GEOM)[0]).x == 1


def test_reprojector_rejects_impossible_and_validates_config():
    t = points([float("inf"), 652000.0], [float("inf"), 6862000.0], crs="EPSG:2154")
    out = run_modifier(Reproject, {"target": "EPSG:4326"}, t)
    assert out["output"].num_rows == 1 and out["Rejected"].num_rows == 1
    assert col(out["Rejected"], "_rejection_reason") == ["reprojection impossible"]
    with pytest.raises(ConfigError, match="cible"):
        run_modifier(Reproject, {}, t)
    with pytest.raises(ConfigError, match="inconnu"):
        run_modifier(Reproject, {"target": "EPSG:99999999"}, t)
