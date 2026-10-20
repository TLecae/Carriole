import pytest
from etl_engine.analysis import analyze
from etl_engine.modifiers.cast_attributes import CastAttributes
from etl_engine.modifiers.convert_date import ConvertDate
from etl_engine.modifiers.extract_json import ExtractJson
from etl_engine.modifiers.extract_text import ExtractText
from etl_engine.modifiers.pivot_longer import PivotLonger
from etl_engine.modifiers.pivot_wider import PivotWider
from etl_engine.modifiers.rename_attributes_by_pattern import RenameAttributesByPattern
from etl_engine.modifiers.replace_text import ReplaceText
from etl_engine.modifiers.route_by_expression import RouteByExpression as MultiFilter
from etl_engine.sdk import ConfigError
from etl_engine.testing import run_modifier, table

from conftest import make_flow


def rows(t):
    return t.to_pylist()


# ------------------------------------------------------------------ RouteByExpression
POPS = table(id=[1, 2, 3, 4, 5, 6], pop=[10, 500, 1500, 20000, None, 700])


def test_test_filter_has_one_output_per_condition_and_a_failed_output():
    params = {
        "tests": [
            {"output": "Grandes", "condition": '"pop" >= 10000'},
            {"output": "Moyennes", "condition": '"pop" >= 500'},
        ]
    }
    assert MultiFilter.ports(MultiFilter.Params.model_validate(params))[1] == (
        "Grandes",
        "Moyennes",
        "Failed",
        "Rejected",
    )
    out = run_modifier(MultiFilter, params, POPS)
    assert [r["id"] for r in rows(out["Grandes"])] == [4]
    assert [r["id"] for r in rows(out["Moyennes"])] == [2, 3, 6]  # « first » : la 4 est déjà partie sur Grandes
    assert [r["id"] for r in rows(out["Failed"])] == [1, 5]  # pop vide : condition nulle = fausse


def test_test_filter_all_mode_duplicates_and_errors_are_rejected():
    params = {
        "mode": "all",
        "tests": [{"output": "A", "condition": '"pop" >= 500'}, {"output": "B", "condition": '"pop" >= 10000'}],
    }
    out = run_modifier(MultiFilter, params, POPS)
    assert [r["id"] for r in rows(out["A"])] == [2, 3, 4, 6] and [r["id"] for r in rows(out["B"])] == [4]
    text = table(id=[1, 2, 3], v=["5", "x", "7"])
    bad = run_modifier(MultiFilter, {"tests": [{"output": "A", "condition": 'CAST("v" AS INTEGER) > 6'}]}, text)
    assert [r["id"] for r in rows(bad["A"])] == [3] and [r["id"] for r in rows(bad["Failed"])] == [1]
    assert [r["id"] for r in rows(bad["Rejected"])] == [2] and "_rejection_reason" in bad["Rejected"].column_names


@pytest.mark.parametrize(
    ("tests", "message"),
    [
        ([{"output": "a b", "condition": "1=1"}], "nom de sortie invalide"),
        ([{"output": "Failed", "condition": "1=1"}], "réservé"),
        ([{"output": "A", "condition": ""}], "pas de condition"),
        ([{"output": "A", "condition": "1=1"}, {"output": "A", "condition": "1=1"}], "en double"),
        ([], "au moins une"),
    ],
)
def test_test_filter_refuses_bad_configuration(tests, message):
    with pytest.raises(ConfigError, match=message):
        run_modifier(MultiFilter, {"tests": tests}, POPS)


def test_the_editor_gets_the_ports_that_depend_on_the_parameters(registry):
    flow = make_flow(
        [
            {
                "id": "t",
                "type": "RouteByExpression",
                "params": {"tests": [{"output": "X", "condition": "1=1"}, {"output": "Y", "condition": "1=0"}]},
            }
        ],
        [],
    )
    result = analyze(flow, registry)
    assert result["ports"]["t"] == {"inputs": ["input"], "outputs": ["X", "Y", "Failed", "Rejected"]}


# ------------------------------------------------------------------ pivot
LONG = table(
    commune=["A", "A", "B", "B", "C"],
    annee=["2020", "2021", "2020", "2021", "2020"],
    pop=[10, 12, 20, 25, 30],
)


def test_pivot_makes_one_column_per_value():
    out = run_modifier(
        PivotWider,
        {"group_by": ["commune"], "pivot_attribute": "annee", "value_attribute": "pop", "prefix": "pop_"},
        LONG,
    )["output"]
    assert out.column_names == ["commune", "pop_2020", "pop_2021"]
    assert rows(out) == [
        {"commune": "A", "pop_2020": 10, "pop_2021": 12},
        {"commune": "B", "pop_2020": 20, "pop_2021": 25},
        {"commune": "C", "pop_2020": 30, "pop_2021": None},
    ]


def test_pivot_functions_and_known_columns_give_the_schema():
    params = {
        "group_by": ["commune"],
        "pivot_attribute": "annee",
        "function": "count",
        "columns": ["2020", "2021", "2022"],
    }
    inst = PivotWider(PivotWider.Params.model_validate(params), None)  # type: ignore[arg-type]
    assert inst.output_schema({"input": LONG.schema})["output"].names == ["commune", "2020", "2021", "2022"]
    out = run_modifier(PivotWider, params, LONG)["output"]
    assert rows(out)[0] == {"commune": "A", "2020": 1, "2021": 1, "2022": 0}
    concat = run_modifier(
        PivotWider,
        {"group_by": [], "pivot_attribute": "annee", "value_attribute": "commune", "function": "concat"},
        LONG,
    )["output"]
    assert rows(concat) == [{"2020": "A, B, C", "2021": "A, B"}]


def test_pivot_refuses_a_missing_attribute():
    with pytest.raises(ConfigError, match="attributs absents"):
        run_modifier(PivotWider, {"pivot_attribute": "nope", "value_attribute": "pop"}, LONG)


# ------------------------------------------------------------------ dépivot
WIDE = table(commune=["A", "B"], pop_2020=[10, None], pop_2021=[12, 25])


def test_unpivot_makes_one_row_per_column_and_skips_empty_values():
    params = {"attributes": ["pop_2020", "pop_2021"], "name_attribute": "annee", "value_attribute": "pop"}
    out = run_modifier(PivotLonger, params, WIDE)["output"]
    assert sorted(rows(out), key=lambda r: (r["commune"], r["annee"])) == [
        {"commune": "A", "annee": "pop_2020", "pop": 10},
        {"commune": "A", "annee": "pop_2021", "pop": 12},
        {"commune": "B", "annee": "pop_2021", "pop": 25},
    ]
    kept = run_modifier(PivotLonger, {**params, "keep_nulls": True}, WIDE)["output"]
    assert kept.num_rows == 4


def test_unpivot_makes_text_when_types_differ():
    mixed = table(id=[1], a=[1], b=["x"])
    out = run_modifier(PivotLonger, {"attributes": ["a", "b"]}, mixed)["output"]
    assert sorted(r["value"] for r in rows(out)) == ["1", "x"]


# ------------------------------------------------------------------ conversion de types
def test_type_conversion_with_rejection_or_null():
    data = table(n=["1", "2", "abc", None], d=["1 234,5", "2,5", "x", None])
    params = {"conversions": [{"attribute": "n", "type": "integer"}]}
    out = run_modifier(CastAttributes, params, data)
    assert (
        out["output"].column("n").to_pylist() == [1, 2, None]
        and out["output"].schema.field("n").type.__str__() == "int64"
    )
    assert out["Rejected"].num_rows == 1
    soft = run_modifier(CastAttributes, {**params, "on_error": "null"}, data)
    assert soft["output"].column("n").to_pylist() == [1, 2, None, None] and soft["Rejected"].num_rows == 0
    decimals = run_modifier(
        CastAttributes,
        {"conversions": [{"attribute": "d", "type": "float", "decimal_comma": True}], "on_error": "null"},
        data,
    )
    assert decimals["output"].column("d").to_pylist() == [1234.5, 2.5, None, None]


def test_boolean_conversion_understands_french():
    data = table(b=["oui", "NON", "1", "0", "vrai", "peut-être"])
    out = run_modifier(
        CastAttributes, {"conversions": [{"attribute": "b", "type": "boolean"}], "on_error": "null"}, data
    )["output"]
    assert out.column("b").to_pylist() == [True, False, True, False, True, None]


# ------------------------------------------------------------------ renommage en masse
def test_bulk_rename_rules():
    data = table(**{"Code INSEE": [1], "Nom commune": ["x"], "Éléments": [1]})
    out = run_modifier(
        RenameAttributesByPattern, {"strip_accents": True, "clean": True, "case": "lower", "prefix": "t_"}, data
    )["output"]
    assert out.column_names == ["t_code_insee", "t_nom_commune", "t_elements"]
    snake = run_modifier(RenameAttributesByPattern, {"case": "snake"}, table(CodeInsee=[1], NomCommune=[2]))["output"]
    assert snake.column_names == ["code_insee", "nom_commune"]
    cut = run_modifier(
        RenameAttributesByPattern,
        {"max_length": 5, "attributes": ["longueur_du_nom"]},
        table(longueur_du_nom=[1], autre=[2]),
    )["output"]
    assert cut.column_names == ["longu", "autre"]
    regex = run_modifier(
        RenameAttributesByPattern, {"find": r"^(\w)_", "replace": r"\1", "regex": True}, table(a_x=[1], b_y=[2])
    )["output"]
    assert regex.column_names == ["ax", "by"]


def test_bulk_rename_refuses_collisions():
    with pytest.raises(ConfigError, match="en double"):
        run_modifier(RenameAttributesByPattern, {"find": "_b", "replace": ""}, table(a=[1], a_b=[2]))


# ------------------------------------------------------------------ texte
def test_string_replacer_literal_regex_and_whole_value():
    data = table(t=["rue de la Paix", "RUE du Bac", None, "N/A"])
    lit = run_modifier(ReplaceText, {"attributes": ["t"], "find": "rue", "replace": "r.", "ignore_case": True}, data)[
        "output"
    ]
    assert lit.column("t").to_pylist() == ["r. de la Paix", "r. du Bac", None, "N/A"]
    rx = run_modifier(
        ReplaceText,
        {"attributes": ["t"], "find": r"\s+(de|du)\s+", "replace": " ", "regex": True, "suffix": "_net"},
        data,
    )["output"]
    assert (
        rx.column("t_net").to_pylist()[:2] == ["rue la Paix", "RUE Bac"]
        and rx.column("t").to_pylist()[0] == "rue de la Paix"
    )
    whole = run_modifier(ReplaceText, {"attributes": ["t"], "find": "N/A", "replace": "", "whole_value": True}, data)[
        "output"
    ]
    assert whole.column("t").to_pylist()[3] == ""
    with pytest.raises(ConfigError, match="expression régulière invalide"):
        run_modifier(ReplaceText, {"attributes": ["t"], "find": "(", "replace": "", "regex": True}, data)


def test_string_extractor_modes():
    data = table(insee=["31555", "09122", None], chemin=["a/b/c", "sans", None])
    sub = run_modifier(ExtractText, {"attribute": "insee", "start": 1, "length": 2, "destination": "dep"}, data)[
        "output"
    ]
    assert sub.column("dep").to_pylist() == ["31", "09", None]
    tail = run_modifier(ExtractText, {"attribute": "insee", "start": -3, "destination": "com"}, data)["output"]
    assert tail.column("com").to_pylist() == ["555", "122", None]
    before = run_modifier(
        ExtractText, {"attribute": "chemin", "mode": "before", "separator": "/", "destination": "x"}, data
    )["output"]
    after = run_modifier(
        ExtractText, {"attribute": "chemin", "mode": "after", "separator": "/", "destination": "y"}, data
    )["output"]
    assert before.column("x").to_pylist() == ["a", None, None] and after.column("y").to_pylist() == ["b/c", None, None]
    rx = run_modifier(
        ExtractText,
        {"attribute": "insee", "mode": "regex", "pattern": r"(?P<dep>\d{2})(?P<com>\d{3})", "prefix": "c_"},
        data,
    )["output"]
    assert rx.column("c_dep").to_pylist() == ["31", "09", None] and rx.column("c_com").to_pylist() == [
        "555",
        "122",
        None,
    ]
    with pytest.raises(ConfigError, match="groupe nommé"):
        run_modifier(ExtractText, {"attribute": "insee", "mode": "regex", "pattern": r"\d+"}, data)


# ------------------------------------------------------------------ dates
def test_date_conversion_formats_and_rejects():
    data = table(d=["11/10/2026", "01/02/2025", "pas une date", None])
    params = {"attribute": "d", "from_format": "%d/%m/%Y", "to": "date", "destination": "iso"}
    out = run_modifier(ConvertDate, params, data)
    assert [str(v) for v in out["output"].column("iso").to_pylist()] == ["2026-10-11", "2025-02-01", "None"]
    assert out["Rejected"].num_rows == 1
    text = run_modifier(ConvertDate, {**params, "to": "text", "to_format": "%Y%m%d", "on_error": "null"}, data)[
        "output"
    ]
    assert text.column("iso").to_pylist() == ["20261011", "20250201", None, None]
    epoch = run_modifier(ConvertDate, {"attribute": "e", "to": "epoch"}, table(e=["1970-01-02"]))["output"]
    assert epoch.column("e").to_pylist() == [86400.0]
    auto = run_modifier(ConvertDate, {"attribute": "e", "to": "datetime"}, table(e=["2026-10-11T08:30:00"]))["output"]
    assert str(auto.column("e").to_pylist()[0]) == "2026-10-11 08:30:00"


# ------------------------------------------------------------------ JSON
JSON = table(id=[1, 2], j=['{"a": {"b": 5}, "t": ["x", "y"]}', "pas du json"])
ITEMS = table(id=[1, 2], j=['{"results": [{"n": "A", "v": 1.5}, {"n": "B", "v": 2}]}', '{"results": []}'])


def test_json_extracts_values_with_types():
    params = {
        "attribute": "j",
        "fields": [{"path": "a.b", "name": "b", "type": "integer"}, {"path": "$.t[1]", "name": "t1"}],
    }
    out = run_modifier(ExtractJson, params, JSON)["output"]
    assert rows(out) == [
        {"id": 1, "j": JSON.column("j")[0].as_py(), "b": 5, "t1": "y"},
        {"id": 2, "j": "pas du json", "b": None, "t1": None},
    ]
    assert out.schema.field("b").type.__str__() == "int64"


def test_json_explodes_an_array_into_features():
    params = {
        "attribute": "j",
        "explode": "results",
        "fields": [{"path": "n", "name": "nom"}, {"path": "v", "name": "val", "type": "float"}],
        "drop_source": True,
    }
    out = run_modifier(ExtractJson, params, ITEMS)["output"]
    assert rows(out) == [{"id": 1, "nom": "A", "val": 1.5}, {"id": 1, "nom": "B", "val": 2.0}]
