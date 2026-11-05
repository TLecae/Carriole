"""Vérification d'un SQL sans l'exécuter, pour l'éditeur SQL de l'interface.

- DuckDB (lecture filtrée à la source, ExecuteSql) : analyse syntaxique seule ;
- base d'une connexion : selon la base (PostgreSQL : plan de la requête, `EXPLAIN`, en lecture seule).

Le résultat situe l'erreur (ligne, colonne, à partir de 1) quand la base la donne.
"""

from __future__ import annotations

import re
from typing import Any

import duckdb

from etl_engine import duck

_CARET = re.compile(r"LINE (\d+): (.*)\n( *)\^")


def result(error: str | None = None, line: int | None = None, column: int | None = None, skipped: str = "") -> dict:
    return {"ok": error is None, "error": error, "line": line, "column": column, "skipped": skipped or None}


def position(statement: str, offset: int) -> tuple[int, int]:
    """Ligne et colonne (à partir de 1) du caractère `offset` (à partir de 0) de `statement`."""
    before = statement[: max(0, offset)]
    return before.count("\n") + 1, offset - (before.rfind("\n") + 1) + 1


def has_variables(statement: str) -> bool:
    return "${" in statement


def check_duckdb(statement: str) -> dict[str, Any]:
    if not statement.strip():
        return result()
    if has_variables(statement):
        return result(skipped="variables")
    con = duck.connect("expr")
    try:
        con.extract_statements(statement)
    except duckdb.ParserException as exc:
        text = str(exc)
        message = text.split("\n", 1)[0].removeprefix("Parser Error: ")
        found = _CARET.search(text)
        if not found:
            return result(message)
        line = int(found.group(1))
        column = len(found.group(3)) - len(f"LINE {found.group(1)}: ") + 1
        return result(message, line, max(1, column))
    finally:
        con.close()
    return result()
