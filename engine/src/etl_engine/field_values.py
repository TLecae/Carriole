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
from etl_engine.data import GEOM, has_geometry
from etl_engine.expr import check_single_expression, needs_spatial, quote, sql_base
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


def _timed(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any], column: str | None = None) -> list[tuple]:
    """Résultat de `sql`, interrompu au-delà de `TIMEOUT` ; les erreurs de DuckDB deviennent des messages (valeurs de
    `column`, sinon comptage)."""
    timer = threading.Timer(TIMEOUT, con.interrupt)
    timer.start()
    try:
        return con.execute(sql, params).fetchall()
    except duckdb.InterruptException:
        if column is None:
            raise ConfigError(f"comptage trop long (plus de {TIMEOUT:.0f} s)") from None
        raise ConfigError(f"valeurs de « {column} » trop longues à lire (plus de {TIMEOUT:.0f} s)") from None
    except duckdb.Error as exc:
        if column is None:
            raise ConfigError(f"comptage impossible : {str(exc).splitlines()[0]}") from None
        raise ConfigError(f"valeurs de « {column} » : {str(exc).splitlines()[0]}") from None
    finally:
        timer.cancel()


def top_values(con: duckdb.DuckDBPyConnection, relation: str, column: str, search: str, limit: int) -> list[dict]:
    """Valeurs de `column` dans `relation`, de la plus fréquente à la moins fréquente ; `search` : celles qui le
    contiennent (sans la casse)."""
    c = quote(column)
    where = f"WHERE CAST({c} AS VARCHAR) ILIKE ?" if search else ""
    rows = _timed(
        con,
        f"SELECT {c} AS v, count(*) AS n FROM {relation} {where} GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {int(limit)}",
        [f"%{search}%"] if search else [],
        column,
    )
    return [{"value": json_value(v), "count": int(n)} for v, n in rows]


def _reader(flow: Flow, registry: Registry, node: str) -> Any:
    """Le lecteur DuckDB `node`, avec les variables du flux résolues comme à l'analyse."""
    from etl_engine.modifiers.file_io import DuckDbReader

    try:
        values = resolve_params(flow, None, system_values(preview=True))
    except ValueError as exc:
        raise ConfigError(str(exc)) from None
    plan = build_plan(flow, registry, values, lambda nid: Context(node_id=nid, params=values))
    inst = plan.nodes.get(node)
    if not isinstance(inst, DuckDbReader):
        raise ConfigError("ce nœud ne lit pas un fichier CSV ou Parquet")
    return inst


def _read(inst: Any) -> tuple[str, Any]:
    found = inst.read_query()
    if found is None:
        raise ConfigError("fichier introuvable ou illisible par DuckDB")
    return found


def reader_values(flow: Flow, registry: Registry, node: str, column: str, search: str = "", limit: int = LIMIT) -> list:
    """Valeurs d'une colonne du fichier que lit le nœud `node` (variables du flux résolues comme à l'analyse)."""
    query, prepare = _read(_reader(flow, registry, node))
    con = duck.connect("inspect")
    try:
        prepare(con)
        return top_values(con, f"({query})", column, search, min(int(limit), 500))
    finally:
        con.close()


def reader_columns(flow: Flow, registry: Registry, node: str) -> list[dict[str, str]]:
    """Colonnes du fichier que lit `node`, avant le choix des attributs : celles que sa requête de lecture propose."""
    schema = _reader(flow, registry, node).file_schema()
    if schema is None:
        raise ConfigError("fichier introuvable ou illisible")
    geometry = has_geometry(schema)
    return [{"name": f.name, "type": "geometry" if geometry and f.name == GEOM else str(f.type)} for f in schema]


def reader_count(flow: Flow, registry: Registry, node: str) -> dict[str, int]:
    """Entités du fichier que lit `node`, et celles que son filtre garde : la requête de lecture se vérifie sans lancer
    le flux. Sans la limite de lignes du nœud."""
    inst = _reader(flow, registry, node)
    query, prepare = _read(inst)
    cond, schema = inst._where(), inst.file_schema()
    con = duck.connect("inspect")
    try:
        prepare(con)
        if cond and schema is not None:
            check_single_expression(cond)
            if needs_spatial([cond]):
                duck.load_spatial(con)
            rows = sql_base(f"({query})", schema, [cond])
            sql = f"SELECT count(*), count(*) FILTER (WHERE COALESCE(({cond}), FALSE)) FROM ({rows})"
        else:
            sql = f"SELECT count(*), count(*) FROM ({query})"
        total, kept = _timed(con, sql, [])[0]
        return {"total": int(total), "kept": int(kept)}
    finally:
        con.close()
