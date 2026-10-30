"""Conventions communes des modificateurs (ADR 0045) : ce qui se vérifie sans données l'est avant le run."""

import pyarrow as pa
import pytest
from etl_engine.data import GEOM, with_crs
from etl_engine.registry import Registry
from etl_engine.sdk import ConfigError, Context

SCHEMA = pa.schema([("id", pa.int64()), ("dep", pa.string()), ("x", pa.float64()), (GEOM, pa.binary())])
WGS84 = with_crs(SCHEMA, "EPSG:4326")

CASES = {
    "Counter": ({"attribute": "n", "group_by": ["absent"]}, SCHEMA, "attribut absent : absent"),
    "FindDuplicates": ({"keys": ["dep", "absent"]}, SCHEMA, "attribut absent : absent"),
    "OrderBy": ({"keys": [{"attribute": "absent"}]}, SCHEMA, "attribut absent : absent"),
    "PointFromXY": ({"x": "x", "y": "y"}, SCHEMA, "attribut absent : y"),
    "Dissolve": ({"group_by": ["absent"]}, SCHEMA, "attribut absent : absent"),
    "Hull": ({"scope": "group", "group_by": ["absent"]}, SCHEMA, "attribut absent : absent"),
    "SetVariables": ({"variables": [{"name": "V", "attribute": "absent"}]}, SCHEMA, "attribut absent : absent"),
    "PivotWider": ({"pivot_attribute": "absent", "value_attribute": "x"}, SCHEMA, "attribut absent : absent"),
    "Buffer": ({"distance": 10}, WGS84, "géographique"),
    "MeasureArea": ({}, WGS84, "géographique"),
    "MeasureLength": ({}, WGS84, "géographique"),
}


@pytest.mark.parametrize("kind", CASES)
def test_configuration_errors_are_found_before_the_run(kind):
    params, schema, message = CASES[kind]
    cls = Registry.discover().get(kind)
    inst = cls(cls.Params.model_validate(params), Context())
    with pytest.raises(ConfigError, match=message):
        inst.output_schema({"input": schema})


def test_join_keys_are_checked_on_both_sides():
    cls = Registry.discover().get("JoinByField")
    inst = cls(cls.Params.model_validate({"base_keys": ["dep"], "join_keys": ["code"]}), Context())
    with pytest.raises(ConfigError, match="clés absentes de « join » : code"):
        inst.output_schema({"base": SCHEMA, "join": SCHEMA})


def test_counter_declares_the_integer_it_writes():
    cls = Registry.discover().get("Counter")
    inst = cls(cls.Params.model_validate({"attribute": "dep"}), Context())
    assert inst.output_schema({"input": SCHEMA})["output"].field("dep").type == pa.int64()
