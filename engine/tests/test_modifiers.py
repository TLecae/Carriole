import pyarrow as pa
import pytest
from etl_engine.modifiers.add_attributes import AddAttributes
from etl_engine.modifiers.extract_by_expression import ExtractByExpression
from etl_engine.modifiers.refactor_attributes import RefactorAttributes
from etl_engine.modifiers.rename_attributes import RenameAttributes
from etl_engine.modifiers.retain_attributes import RetainAttributes
from etl_engine.sdk import ConfigError
from etl_engine.testing import points, run_modifier, table


def col(t: pa.Table, name: str) -> list:
    return t.column(name).to_pylist()


def test_creator_sequential_expressions_and_types():
    t = table(a=[1, 2, 3, 4, 5, 6, 7], nom=["x"] * 7)
    out = run_modifier(
        AddAttributes,
        {
            "attributes": [
                {"name": "double", "expression": '"a" * 2'},
                {"name": "plus", "expression": '"double" + 1', "type": "text"},
            ]
        },
        t,
    )["output"]
    assert col(out, "double") == [2, 4, 6, 8, 10, 12, 14]
    assert col(out, "plus") == ["3", "5", "7", "9", "11", "13", "15"]
    assert out.schema.field("plus").type == pa.string()


def test_creator_replaces_existing_attribute_in_place():
    out = run_modifier(
        AddAttributes, {"attributes": [{"name": "a", "expression": '"a" + 10'}]}, table(a=[1, 2], b=["x", "y"])
    )["output"]
    assert out.column_names == ["a", "b"] and col(out, "a") == [11, 12]


def test_creator_rejects_rows_in_error_with_reason():
    t = table(a=[1, 0, 2, 0, 4], b=[10, 10, 10, 10, 10])
    res = run_modifier(
        AddAttributes,
        {"attributes": [{"name": "r", "expression": "CAST(10 / \"a\" AS INTEGER) + CAST('z' AS INTEGER)"}]},
        t,
    )
    # toutes les lignes échouent (conversion de 'z') : aucune sortie, tout est rejeté avec un motif
    assert res["output"].num_rows == 0
    assert res["Rejected"].num_rows == 5
    assert "_rejection_reason" in res["Rejected"].column_names


def test_creator_partial_rejection_keeps_good_rows():
    t = table(v=["1", "2", "oups", "4", "5"])
    res = run_modifier(
        AddAttributes, {"attributes": [{"name": "n", "expression": 'CAST("v" AS INTEGER)'}]}, t, batch_rows=10
    )
    assert col(res["output"], "n") == [1, 2, 4, 5]
    assert col(res["Rejected"], "v") == ["oups"]
    assert "n" in col(res["Rejected"], "_rejection_reason")[0]


def test_creator_typed_conversion_failure_goes_to_rejected():
    t = table(v=["1", "x", "3"])
    res = run_modifier(
        AddAttributes, {"attributes": [{"name": "n", "expression": '"v"', "type": "integer"}]}, t, batch_rows=10
    )
    assert col(res["output"], "n") == [1, 3]
    assert col(res["Rejected"], "v") == ["x"]


def test_creator_invalid_expression_is_config_error():
    with pytest.raises(ConfigError):
        run_modifier(AddAttributes, {"attributes": [{"name": "n", "expression": '"a" +* 2'}]}, table(a=[1]))
    with pytest.raises(ConfigError):
        run_modifier(AddAttributes, {"attributes": [{"name": "n", "expression": '"inconnue" + 1'}]}, table(a=[1]))


def test_expression_must_be_single_statement():
    with pytest.raises(ConfigError):
        run_modifier(
            AddAttributes, {"attributes": [{"name": "n", "expression": "1); DROP TABLE x; SELECT (1"}]}, table(a=[1])
        )


@pytest.mark.parametrize("evil", ["1) OR (1=1", "(1", "1)", "'abc", 'a" + 1'])
def test_expression_cannot_escape_its_parentheses(evil):
    with pytest.raises(ConfigError):
        run_modifier(AddAttributes, {"attributes": [{"name": "n", "expression": evil}]}, table(a=[1]))


def test_balanced_check_ignores_parentheses_in_strings():
    spec = {"attributes": [{"name": "n", "expression": "'(' || \"a\" || ')'"}]}
    assert col(run_modifier(AddAttributes, spec, table(a=["x"]))["output"], "n") == ["(x)"]


def test_creator_geometry_expression():
    t = points([0.0, 1.0], [0.0, 1.0], nom=["a", "b"])
    out = run_modifier(AddAttributes, {"attributes": [{"name": "x", "expression": "ST_X(geometry)"}]}, t)["output"]
    assert col(out, "x") == [0.0, 1.0]


def test_tester_splits_and_combines():
    t = table(pop=[10, 2000, 500, None, 3000], dep=["31", "31", "09", "31", "09"])
    res = run_modifier(
        ExtractByExpression, {"conditions": ['"pop" >= 1000', "\"dep\" = '31'"], "combine": "AND"}, t, batch_rows=2
    )
    assert col(res["Passed"], "pop") == [2000]
    assert sorted(col(res["Failed"], "pop"), key=lambda v: (v is None, v)) == [10, 500, 3000, None]
    res_or = run_modifier(
        ExtractByExpression, {"conditions": ['"pop" >= 1000', "\"dep\" = '31'"], "combine": "OR"}, t, batch_rows=2
    )
    assert res_or["Passed"].num_rows == 4  # le NULL de pop est « faux » mais dep = '31' passe


def test_tester_needs_a_condition_and_boolean():
    with pytest.raises(ConfigError):
        run_modifier(ExtractByExpression, {"conditions": []}, table(a=[1]))
    with pytest.raises(ConfigError, match="booléenne"):
        run_modifier(ExtractByExpression, {"conditions": ['"a" + 1']}, table(a=[1]))


def test_tester_rejects_failing_rows():
    t = table(v=["1", "x", "3"])
    res = run_modifier(ExtractByExpression, {"conditions": ['CAST("v" AS INTEGER) > 0']}, t, batch_rows=10)
    assert col(res["Passed"], "v") == ["1", "3"]
    assert col(res["Rejected"], "v") == ["x"]


def test_renamer_swap_and_errors():
    t = table(a=[1], b=[2])
    out = run_modifier(
        RenameAttributes, {"renamings": [{"source": "a", "target": "b"}, {"source": "b", "target": "a"}]}, t
    )["output"]
    assert out.column_names == ["b", "a"]
    with pytest.raises(ConfigError, match="absents?"):
        run_modifier(RenameAttributes, {"renamings": [{"source": "zz", "target": "y"}]}, t)
    with pytest.raises(ConfigError, match="double"):
        run_modifier(RenameAttributes, {"renamings": [{"source": "a", "target": "b"}]}, t)


def test_keeper_order_and_geometry():
    t = points([1.0], [2.0], a=[1], b=[2], c=[3])
    out = run_modifier(RetainAttributes, {"attributes": ["c", "a"]}, t)["output"]
    assert out.column_names == ["c", "a", "geometry"]
    out = run_modifier(RetainAttributes, {"attributes": ["c"], "keep_geometry": False}, t)["output"]
    assert out.column_names == ["c"]
    with pytest.raises(ConfigError):
        run_modifier(RetainAttributes, {"attributes": ["nope"]}, t)


def test_manager_rules_in_order():
    t = table(a=[1, 2], b=["x", "y"], c=[0, 0])
    rules = [
        {"action": "set", "attribute": "d", "expression": '"a" * 10'},
        {"action": "rename", "attribute": "b", "new_name": "nom"},
        {"action": "remove", "attribute": "c"},
    ]
    out = run_modifier(RefactorAttributes, {"rules": rules}, t)["output"]
    assert out.column_names == ["a", "nom", "d"]
    assert col(out, "d") == [10, 20]


def test_output_schema_without_running():
    from etl_engine.sdk import Context

    inst = AddAttributes(
        AddAttributes.Params.model_validate({"attributes": [{"name": "z", "expression": '"a" * 1.5'}]}), Context()
    )
    schema = inst.output_schema({"input": pa.schema([("a", pa.int64())])})["output"]
    assert (
        schema.names == ["a", "z"]
        and pa.types.is_floating(schema.field("z").type)
        or pa.types.is_decimal(schema.field("z").type)
    )
