"""Évaluation d'expressions SQL DuckDB sur un lot Arrow.

Les expressions utilisateur (attributs calculés, conditions) sont des expressions SQL DuckDB
sur les colonnes du lot. La géométrie WKB est exposée comme GEOMETRY DuckDB sous le nom
`geometry` : `ST_Area(geometry)`, `ST_Intersects(geometry, ST_Point(0, 0))`…

Sécurité : une expression doit être un SQL à instruction unique ; la connexion n'a ni accès
fichiers ni réseau (verrouillée après le chargement de l'extension spatiale).

Voie différée (ADR 0044) : un modificateur ligne à ligne écrit sa requête avec les mêmes expressions, chacune
marquée par `guarded` ; `sql_base` et `sql_columns` donnent la même vue des lignes que `evaluate`.
"""

from __future__ import annotations

import re
import threading
from typing import Literal

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

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


_GUARD = "etl_guard"
# fonctions dont le résultat change d'une lecture à l'autre : un port qui en dépend ne peut pas être relu à la demande
_VOLATILE = re.compile(
    r"\b(random|gen_random_uuid|uuid|uuidv4|uuidv7|now|current_timestamp|current_date|current_time|today|"
    r"get_current_time|get_current_timestamp|transaction_timestamp|nextval|currval|setseed)\s*\(",
    re.IGNORECASE,
)


def deterministic(expression: str) -> bool:
    """Vrai si l'expression donne toujours le même résultat sur les mêmes lignes."""
    return not _VOLATILE.search(expression)


def guarded(expression: str) -> str:
    """Expression d'utilisateur dans une requête de la voie différée : évaluée telle quelle au contrôle (une erreur
    de donnée renvoie le nœud vers la voie par lots), dans `TRY(…)` dans la définition (`unguard`)."""
    return f"{_GUARD}({expression})"


def unguard(sql: str, mode: Literal["check", "define"]) -> str:
    """La requête au contrôle (expressions telles quelles) ou dans la définition (`TRY`). Une fois le contrôle passé
    sur toutes les lignes qui atteignent le nœud, `TRY` ne change rien à leurs valeurs ; il ne sert que si DuckDB,
    dans une chaîne composée, évalue une condition avant le filtre amont, sur des lignes que ce filtre écarte."""
    return sql.replace(f"{_GUARD}(", "TRY(" if mode == "define" else "(")


def needs_spatial(expressions: list[str]) -> bool:
    return any(_NEEDS_SPATIAL.search(t) for t in expressions)


def quote(name: str) -> str:
    """Identifiant SQL entre guillemets."""
    return '"' + name.replace('"', '""') + '"'


_WKB = "__etl_wkb"


def sql_base(relation: str, schema: pa.Schema, expressions: list[str]) -> str:
    """Les lignes de `relation` comme `evaluate` les montre aux expressions : `geometry` en GEOMETRY si l'une d'elles
    s'en sert, le WKB d'origine gardé à côté pour la sortie (`sql_columns`)."""
    if GEOM in schema.names and needs_spatial(expressions):
        g = quote(GEOM)
        return f"SELECT * REPLACE (ST_GeomFromWKB({g}) AS {g}), {g} AS {_WKB} FROM {relation}"
    return f"SELECT * FROM {relation}"


def sql_columns(names: list[str], schema: pa.Schema, expressions: list[str]) -> str:
    """Colonnes de sortie `names`, dans cet ordre, sur une requête bâtie par `sql_base` : la géométrie d'origine (WKB)
    reprend sa place."""
    converted = GEOM in schema.names and needs_spatial(expressions)
    return ", ".join(f"{_WKB} AS {quote(GEOM)}" if n == GEOM and converted else quote(n) for n in names)


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


def _prepare(columns: list[str], expressions: dict[str, str]) -> tuple[duckdb.DuckDBPyConnection, str, str]:
    """(connexion du thread, liste du SELECT, requête de base sur `src`) pour évaluer `expressions`."""
    for text in expressions.values():
        check_single_expression(text)
    needs = any(_NEEDS_SPATIAL.search(t) for t in expressions.values())
    con, spatial = _connection()
    if needs and not spatial:
        raise ConfigError("l'extension spatiale DuckDB est indisponible (installation impossible)")
    base = "SELECT * FROM src"
    if needs and GEOM in columns:
        base = f'SELECT * REPLACE (ST_GeomFromWKB("{GEOM}") AS "{GEOM}") FROM src'
    return con, ", ".join(f'({text}) AS "{name}"' for name, text in expressions.items()), base


def _run(con: duckdb.DuckDBPyConnection, sql: str) -> pa.Table:
    try:
        return con.sql(sql).to_arrow_table()
    except _CONFIG_ERRORS as exc:
        raise ConfigError(f"expression invalide : {exc}") from exc
    except duckdb.Error as exc:
        if (found := duck.resource_error(exc)) is not None:  # pas une erreur de donnée : aucune ligne n'est rejetée
            raise found from exc
        raise ExpressionError(str(exc)) from exc


def evaluate(table: pa.Table, expressions: dict[str, str]) -> pa.Table:
    """Évalue plusieurs expressions indépendantes ; renvoie une table (une colonne par nom).

    `ConfigError` si une expression est invalide ; `ExpressionError` si elle échoue sur les données.
    """
    con, select, base = _prepare(table.column_names, expressions)
    con.register("src", table)
    try:
        return _run(con, f"SELECT {select} FROM ({base})")
    finally:
        con.unregister("src")


def expression_type(schema: pa.Schema, expression: str) -> pa.DataType:
    """Type du résultat de `expression` sur des lignes de ce schéma (évaluée sur une table vide)."""
    return evaluate(schema.empty_table(), {"v": expression}).schema.field("v").type


def evaluate_one(table: pa.Table, expression: str) -> pa.ChunkedArray:
    return evaluate(table, {"v": expression}).column("v")


def safe_evaluate(table: pa.Table, expressions: dict[str, str]) -> tuple[pa.Table, dict[int, str]]:
    """Comme `evaluate`, mais isole les lignes en erreur.

    Renvoie (résultats, {indice de ligne: message}). Les lignes en erreur ont des valeurs nulles.
    Rien de plus n'est payé sans erreur. Sinon : un passage avec `TRY(…)` donne toutes les valeurs, NULL pour les lignes
    en erreur ; seules les lignes à NULL peuvent l'être, et elles seules sont examinées (`_isolate`).
    """
    try:
        return evaluate(table, expressions), {}
    except ExpressionError:
        pass
    try:
        tried = evaluate(table, {name: f"TRY({text})" for name, text in expressions.items()})
    except ExpressionError:  # une erreur que TRY ne rattrape pas : tout le lot est examiné
        errors = _isolate(table, expressions)
        good = np.ones(table.num_rows, dtype=bool)
        good[list(errors)] = False
        kept = evaluate(table.filter(pa.array(good)), expressions)
        index = np.cumsum(good) - 1  # ligne de `kept` de chaque ligne gardée
        return kept.take(pa.array(index, pa.int64(), mask=~good)), errors
    suspect = np.zeros(table.num_rows, dtype=bool)
    for column in tried.columns:
        suspect |= pc.is_null(column).to_numpy(zero_copy_only=False)
    rows = np.flatnonzero(suspect)
    found = _isolate(table.take(pa.array(rows, pa.int64())), expressions)
    return tried, {int(rows[i]): message for i, message in found.items()}


ROW_BY_ROW = 8  # une plage en erreur de cette taille ou moins est examinée ligne par ligne


def _isolate(table: pa.Table, expressions: dict[str, str]) -> dict[int, str]:
    """{ligne en erreur: message}, par découpage en deux des plages qui échouent ; la table n'est enregistrée qu'une
    fois, chaque essai filtre ses lignes (le coût n'est payé que là où une erreur survient)."""
    con, select, base = _prepare(table.column_names, expressions)
    numbered = table.append_column("__row", pa.array(np.arange(table.num_rows), pa.int64()))
    head = f"SELECT {select} FROM ({base}) WHERE __row >= "  # expressions d'utilisateur : pas de str.format
    errors: dict[int, str] = {}

    def fails(start: int, stop: int) -> str | None:
        try:
            _run(con, f"{head}{int(start)} AND __row < {int(stop)}")
            return None
        except ExpressionError as exc:
            return str(exc)

    def look(start: int, stop: int) -> None:
        if stop - start <= ROW_BY_ROW:
            for row in range(start, stop):
                if (message := fails(row, row + 1)) is not None:
                    errors[row] = message
            return
        if fails(start, stop) is None:
            return
        mid = (start + stop) // 2
        look(start, mid)
        look(mid, stop)

    con.register("src", numbered)
    try:
        look(0, table.num_rows)
    finally:
        con.unregister("src")
    return errors


def safe_mask(table: pa.Table, expression: str) -> tuple[pa.Array, dict[int, str]]:
    """Condition booléenne par ligne (NULL et erreur = faux) et lignes en erreur."""
    result, errors = safe_evaluate(table, {"v": expression})
    col = result.column("v")
    if not pa.types.is_boolean(col.type):
        raise ConfigError(f"la condition « {expression} » doit être booléenne (type obtenu : {col.type})")
    return col.combine_chunks().fill_null(False), errors
