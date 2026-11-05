"""Vérification d'un SQL sans l'exécuter (éditeur SQL de l'interface)."""

from etl_engine import sql_check


def test_duckdb_syntax_error_is_located():
    got = sql_check.check_duckdb("SELECT a,\n  b FROM t WHERE (x = 1")
    assert got["ok"] is False and "syntax error" in got["error"]
    assert got["line"] == 2 and got["column"] >= 1


def test_duckdb_valid_or_empty_sql_passes():
    assert sql_check.check_duckdb("SELECT * FROM t WHERE x > 1")["ok"] is True
    assert sql_check.check_duckdb("  ")["ok"] is True


def test_variables_are_not_checked():
    got = sql_check.check_duckdb("SELECT * FROM t WHERE dep = '${DEP}' FORM")
    assert got["ok"] is True and got["skipped"] == "variables"


def test_position_gives_line_and_column():
    assert sql_check.position("SELECT 1\nFROM t", 9) == (2, 1)
    assert sql_check.position("SELECT 1", 7) == (1, 8)
