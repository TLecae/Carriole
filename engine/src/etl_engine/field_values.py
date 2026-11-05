"""Valeurs les plus fréquentes d'un attribut, pour l'aide à la saisie des éditeurs (SQL, expressions, filtres).

L'éditeur montre les attributs (métadonnées) et, à la demande, leurs valeurs : on construit une requête sans deviner
ni l'orthographe d'un nom ni celle d'une valeur. Ici : les valeurs d'une colonne du fichier qu'un lecteur DuckDB lit
(avant son propre filtre). Celles d'une table d'une base sont dans les modules des bases, celles d'un port d'un run
dans l'inspecteur.
"""

from __future__ import annotations

import datetime
import decimal
import threading
from typing import Any

import duckdb

from etl_engine import duck
from etl_engine.expr import quote
from etl_engine.flow import Flow, resolve_params, system_values
from etl_engine.plan import build_plan
from etl_engine.registry import Registry
from etl_engine.sdk import ConfigError, Context

LIMIT = 50  # valeurs proposées au plus
TIMEOUT = 20.0  # secondes : un très gros fichier distant ne bloque pas l'éditeur


def json_value(value: Any) -> Any:
    """Valeur transmise à l'éditeur : nombres, booléens et vide tels quels, le reste en texte (dates…)."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, datetime.date | datetime.datetime | datetime.time):
        return value.isoformat(sep=" ") if isinstance(value, datetime.datetime) else value.isoformat()
    return str(value)


def top_values(con: duckdb.DuckDBPyConnection, relation: str, column: str, search: str, limit: int) -> list[dict]:
    """Valeurs de `column` dans `relation`, de la plus fréquente à la moins fréquente ; `search` : celles qui le
    contiennent (sans la casse)."""
    c = quote(column)
    where = f"WHERE CAST({c} AS VARCHAR) ILIKE ?" if search else ""
    timer = threading.Timer(TIMEOUT, con.interrupt)
    timer.start()
    try:
        rows = con.execute(
            f"SELECT {c} AS v, count(*) AS n FROM {relation} {where} GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {int(limit)}",
            [f"%{search}%"] if search else [],
        ).fetchall()
    except duckdb.InterruptException:
        raise ConfigError(f"valeurs de « {column} » trop longues à lire (plus de {TIMEOUT:.0f} s)") from None
    except duckdb.Error as exc:
        raise ConfigError(f"valeurs de « {column} » : {str(exc).splitlines()[0]}") from None
    finally:
        timer.cancel()
    return [{"value": json_value(v), "count": int(n)} for v, n in rows]


def reader_values(flow: Flow, registry: Registry, node: str, column: str, search: str = "", limit: int = LIMIT) -> list:
    """Valeurs d'une colonne du fichier que lit le nœud `node` (variables du flux résolues comme à l'analyse)."""
    from etl_engine.modifiers.file_io import DuckDbReader

    try:
        values = resolve_params(flow, None, system_values(preview=True))
    except ValueError as exc:
        raise ConfigError(str(exc)) from None
    plan = build_plan(flow, registry, values, lambda nid: Context(node_id=nid, params=values))
    inst = plan.nodes.get(node)
    if not isinstance(inst, DuckDbReader):
        raise ConfigError("valeurs indisponibles : ce nœud ne lit pas un fichier CSV ou Parquet")
    found = inst.read_query()
    if found is None:
        raise ConfigError("valeurs indisponibles : fichier introuvable ou illisible par DuckDB")
    query, prepare = found
    con = duck.connect("inspect")
    try:
        prepare(con)
        return top_values(con, f"({query})", column, search, min(int(limit), 500))
    finally:
        con.close()
