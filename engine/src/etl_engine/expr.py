"""Évaluation d'expressions SQL DuckDB sur un lot Arrow.

Les expressions utilisateur (attributs calculés, conditions) sont des expressions SQL DuckDB
sur les colonnes du lot. La géométrie WKB est exposée comme GEOMETRY DuckDB sous le nom
`geometry` : `ST_Area(geometry)`, `ST_Intersects(geometry, ST_Point(0, 0))`…

Sécurité : une expression doit être un SQL à instruction unique ; la connexion n'a ni accès
fichiers ni réseau (verrouillée après le chargement de l'extension spatiale).
"""

from __future__ import annotations

import re
import threading

import duckdb
import pyarrow as pa

from etl_engine import duck
from etl_engine.data import GEOM
from etl_engine.sdk import ConfigError

_local = threading.local()
_NEEDS_SPATIAL = re.compile(r"\bST_|\bgeometry\b", re.IGNORECASE)


def _connection() -> tuple[duckdb.DuckDBPyConnection, bool]:
    """Connexion du thread courant et indicateur « spatial chargé »."""
    con = getattr(_local, "con", None)
    if con is None:
        con = duck.connect("expr")  # un thread, une petite part du budget : le parallélisme vient des lots
        spatial = False
        if duck.spatial_available():
            try:
                duck.load_spatial(con)
                spatial = True
            except duckdb.Error:
                spatial = False
        con.execute("SET enable_external_access=false")
        con.execute("SET lock_configuration=true")
        _local.con, _local.spatial = con, spatial
    return con, _local.spatial


def _balanced(text: str) -> bool:
    """Parenthèses équilibrées hors chaînes 'ici' et identifiants "ici".

    L'expression est insérée entre parenthèses dans une requête plus grande : sans ce contrôle,
    `1) OR (1=1` en sortirait et changerait la priorité des autres conditions.
    """
    depth, quote = 0, ""
    for ch in text:
        if quote:
            if ch == quote:  # « '' » à l'intérieur d'une chaîne se referme puis se rouvre aussitôt
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not quote


def check_single_expression(expression: str) -> None:
    """Refuse tout ce qui n'est pas une expression SQL unique (utilisé aussi par l'inspecteur)."""
    text = expression.strip()
    if not text:
        raise ConfigError("expression vide")
    if not _balanced(text):
        raise ConfigError(f"parenthèses ou guillemets déséquilibrés dans « {text} »")
    try:
        statements = duckdb.extract_statements(f"SELECT ({text}) AS v")
    except duckdb.Error as exc:
        raise ConfigError(f"expression invalide « {text} » : {exc}") from exc
    if len(statements) != 1:
        raise ConfigError(f"l'expression « {text} » doit être une expression unique")


class ExpressionError(Exception):
    """Une expression, correcte en soi, échoue sur certaines données (division par zéro, conversion…)."""


_CONFIG_ERRORS = (duckdb.ParserException, duckdb.BinderException, duckdb.CatalogException)


def evaluate(table: pa.Table, expressions: dict[str, str]) -> pa.Table:
    """Évalue plusieurs expressions indépendantes ; renvoie une table (une colonne par nom).

    `ConfigError` si une expression est invalide ; `ExpressionError` si elle échoue sur les données.
    """
    for text in expressions.values():
        check_single_expression(text)
    needs = any(_NEEDS_SPATIAL.search(t) for t in expressions.values())
    con, spatial = _connection()
    if needs and not spatial:
        raise ConfigError("l'extension spatiale DuckDB est indisponible (installation impossible)")
    base = "SELECT * FROM src"
    if needs and GEOM in table.column_names:
        base = f'SELECT * REPLACE (ST_GeomFromWKB("{GEOM}") AS "{GEOM}") FROM src'
    select = ", ".join(f'({text}) AS "{name}"' for name, text in expressions.items())
    con.register("src", table)
    try:
        return con.execute(f"SELECT {select} FROM ({base})").to_arrow_table()
    except _CONFIG_ERRORS as exc:
        raise ConfigError(f"expression invalide : {exc}") from exc
    except duckdb.OutOfMemoryException as exc:  # pas une erreur de donnée : les lignes ne doivent pas être rejetées
        raise MemoryError(str(exc)) from exc
    except duckdb.Error as exc:
        raise ExpressionError(str(exc)) from exc
    finally:
        con.unregister("src")


def evaluate_one(table: pa.Table, expression: str) -> pa.ChunkedArray:
    return evaluate(table, {"v": expression}).column("v")


def safe_evaluate(table: pa.Table, expressions: dict[str, str]) -> tuple[pa.Table, dict[int, str]]:
    """Comme `evaluate`, mais isole les lignes en erreur.

    Renvoie (résultats, {indice de ligne: message}). Les lignes en erreur ont des valeurs nulles.
    Le coût de l'isolement (découpage en deux, récursif) n'est payé que si une erreur survient.
    """
    try:
        return evaluate(table, expressions), {}
    except ExpressionError as exc:
        n = table.num_rows
        if n <= 1:
            types = evaluate(table.slice(0, 0), expressions).schema
            nulls = pa.Table.from_arrays([pa.nulls(n, f.type) for f in types], schema=types)
            return nulls, ({0: str(exc)} if n else {})
        mid = n // 2
        left, left_err = safe_evaluate(table.slice(0, mid), expressions)
        right, right_err = safe_evaluate(table.slice(mid), expressions)
        errors = {**left_err, **{i + mid: m for i, m in right_err.items()}}
        return pa.concat_tables([left, right]), errors


def safe_mask(table: pa.Table, expression: str) -> tuple[pa.Array, dict[int, str]]:
    """Condition booléenne par ligne (NULL et erreur = faux) et lignes en erreur."""
    result, errors = safe_evaluate(table, {"v": expression})
    col = result.column("v")
    if not pa.types.is_boolean(col.type):
        raise ConfigError(f"la condition « {expression} » doit être booléenne (type obtenu : {col.type})")
    return col.combine_chunks().fill_null(False), errors
