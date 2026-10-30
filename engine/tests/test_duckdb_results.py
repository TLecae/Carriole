"""DuckDB 2 : les expressions des flux donnent les mêmes résultats qu'avec DuckDB 1.5 (`duck.keep_results`)."""

import pyarrow as pa
from etl_engine import expr
from etl_engine.modifiers.add_attributes import AddAttributes
from etl_engine.modifiers.file_io import GeoPackageWriter
from etl_engine.testing import run_modifier


def test_expressions_keep_their_duckdb_1_5_results():
    table = pa.table({"n": [5, 7], "code": ["31555", "abc"]})
    out = expr.evaluate(table, {"vide": "NULL", "q": '"n" // 0', "r": '"n" % 0', "m": "\"code\" ~ '31'"})
    assert out.schema.field("vide").type == pa.int32()  # pas une colonne sans type
    assert out.column("q").to_pylist() == out.column("r").to_pylist() == [None, None]  # pas une erreur
    assert out.column("m").to_pylist() == [False, False]  # toute la valeur, comme avant


def test_an_empty_attribute_can_be_written(tmp_path):
    table = pa.table({"id": [1, 2]})
    out = run_modifier(AddAttributes, {"attributes": [{"name": "vide", "expression": "NULL"}]}, {"input": table})
    run_modifier(GeoPackageWriter, {"path": str(tmp_path / "out.gpkg")}, {"input": out["output"]})
    assert (tmp_path / "out.gpkg").stat().st_size > 0
