"""Requêtes SQL DuckDB utilisateur sur des tables Arrow (ExecuteSql) : une seule instruction SELECT, sans accès
aux fichiers ni au réseau, la géométrie vue comme GEOMETRY.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

import duckdb
import pyarrow as pa

from etl_engine import duck
from etl_engine.data import GEOM, get_crs, with_crs
from etl_engine.sdk import ConfigError

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def check_select(sql: str) -> str:
    """Refuse tout ce qui n'est pas une instruction SELECT (ou WITH … SELECT) unique."""
    text = sql.strip().rstrip(";").strip()
    if not text:
        raise ConfigError("requête SQL vide")
    try:
        statements = duckdb.extract_statements(text)
    except duckdb.Error as exc:
        raise ConfigError(f"requête SQL invalide : {exc}") from exc
    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
        raise ConfigError("la requête doit être une seule instruction SELECT")
    return text


def final_select(text: str, described: list[tuple]) -> str:
    """Le résultat de la requête `text` sous la forme des ports, quelle que soit la voie : géométrie en WKB, entiers
    de 128 bits (une somme d'entiers) en DOUBLE, comme les écrit le format Parquet de DuckDB."""
    swaps = []
    for row in described:
        kind = str(row[1]).upper()
        if kind == "GEOMETRY":
            swaps.append(f'ST_AsWKB("{row[0]}") AS "{row[0]}"')
        elif kind in ("HUGEINT", "UHUGEINT"):
            swaps.append(f'CAST("{row[0]}" AS DOUBLE) AS "{row[0]}"')
    return f"SELECT * REPLACE ({', '.join(swaps)}) FROM ({text})" if swaps else f"SELECT * FROM ({text})"


def describe(sql: str, schemas: Mapping[str, pa.Schema]) -> list[tuple]:
    """Colonnes et types du résultat de `sql` (chaque entrée vue comme dans `run_query`), sans lire de données."""
    con = duck.connect("expr")
    try:
        duck.load_spatial(con)
        for name, schema in schemas.items():
            con.register(f"__raw_{name}", schema.empty_table())
            geom = f'ST_GeomFromWKB("{GEOM}")' if GEOM in schema.names else None
            body = f'SELECT * REPLACE ({geom} AS "{GEOM}")' if geom else "SELECT *"
            con.execute(f'CREATE VIEW "{name}" AS {body} FROM "__raw_{name}"')
        return con.execute(f"DESCRIBE SELECT * FROM ({check_select(sql)})").fetchall()
    except duckdb.Error as exc:
        raise ConfigError(f"requête SQL invalide : {exc}") from exc
    finally:
        con.close()


def run_query(sql: str, tables: Mapping[str, pa.Table]) -> pa.Table:
    """Exécute `sql` ; chaque table est visible sous son nom (la colonne `geometry` en GEOMETRY DuckDB)."""
    text = check_select(sql)
    con = duck.connect("heavy")
    spatial = False
    if duck.spatial_available():
        try:
            duck.load_spatial(con)
            spatial = True
        except duckdb.Error:
            spatial = False
    try:
        for name, table in tables.items():
            if not IDENT.match(name):
                raise ConfigError(f"nom d'entrée invalide : « {name} »")
            raw = f"__raw_{name}"
            con.register(raw, table)
            if spatial and GEOM in table.column_names:
                con.execute(
                    f'CREATE VIEW "{name}" AS SELECT * REPLACE (ST_GeomFromWKB("{GEOM}") AS "{GEOM}") FROM "{raw}"'
                )
            else:
                con.execute(f'CREATE VIEW "{name}" AS SELECT * FROM "{raw}"')
        con.execute("SET enable_external_access=false")
        con.execute("SET lock_configuration=true")
        try:
            described = con.execute(f"DESCRIBE SELECT * FROM ({text})").fetchall()
            out = con.sql(final_select(text, described)).to_arrow_table()
        except (duckdb.ParserException, duckdb.BinderException, duckdb.CatalogException) as exc:
            raise ConfigError(f"requête SQL invalide : {exc}") from exc
        except duckdb.Error as exc:
            raise ConfigError(f"requête SQL en échec : {exc}") from exc
    finally:
        con.close()
    crs = next((c for c in (get_crs(t) for t in tables.values()) if c), None)
    return with_crs(out, crs) if GEOM in out.column_names else out
