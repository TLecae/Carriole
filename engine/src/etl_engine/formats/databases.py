"""Une base de données quel que soit son type : la connexion nommée dit lequel (`ConnectionInfo.kind`).

PostgreSQL / PostGIS : `pg.py` ; Oracle : `oracle.py` (sur `dbapi.py`). Tous offrent la même interface aux
modificateurs (lecture, écriture, SQL libre) et à l'API (essai, liste des tables).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

import pyarrow as pa

from etl_engine import connections
from etl_engine.sdk import ConfigError


def kind_of(connection: str) -> str:
    return connections.resolve(connection).kind


def backend(connection: str) -> Any:
    """Le module de la base de `connection` (`Database` de dbapi, ou l'adaptateur PostgreSQL)."""
    kind = kind_of(connection)
    if kind == "postgres":
        return POSTGRES
    if kind == "oracle":
        from etl_engine.formats.oracle import database

        return database
    raise ConfigError(f"type de base non pris en charge : {kind}")


class _PgSession:
    """SQL libre sur PostgreSQL, même interface que `dbapi.Session`."""

    def __init__(self, connection: str, read_only: bool) -> None:
        from etl_engine.formats import pg

        self.pg, self.read_only = pg, read_only
        self.conn = pg.connect(connection, read_only=read_only)

    def execute(self, statement: str, params: dict[str, Any]) -> pa.Table | None:
        import psycopg

        with self.conn.cursor() as cur:
            try:
                cur.execute(statement, params or None)
            except psycopg.errors.Error as exc:
                raise ConfigError(f"SQL : {self.pg._first_line(exc)}") from None
            return self.pg.result_table(cur, cur.fetchall()) if cur.description else None

    def describe(self, statement: str, names: Sequence[str]) -> pa.Schema:
        table = self.execute(f"SELECT * FROM ({statement}) AS _q LIMIT 0", dict.fromkeys(names))
        return table.schema if table is not None else pa.schema([])

    def commit(self) -> None:
        if not self.read_only:
            self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def close(self) -> None:
        try:
            self.conn.rollback()
        finally:
            self.conn.close()


class _Postgres:
    """Adaptateur de `pg.py` à l'interface commune."""

    label = "PostgreSQL"

    @property
    def pg(self):
        from etl_engine.formats import pg

        return pg

    def read_schema(self, connection: str, **options: Any) -> pa.Schema:
        return self.pg.read_schema(connection, **options)

    def read_batches(self, connection: str, batch_rows: int, **options: Any) -> Iterator[pa.Table]:
        return self.pg.read_batches(connection, batch_rows, **options)

    def list_tables(self, connection: str) -> list[dict[str, Any]]:
        return self.pg.list_tables(connection)

    def check_connection(self, connection: str) -> dict[str, Any]:
        info = self.pg.check_connection(connection)
        return {**info, "spatial": f"PostGIS {info['postgis']}" if info.get("postgis") else None}

    def writer(self, connection: str, table: str, **options: Any) -> Any:
        return self.pg.PgWriter(connection, table, **options)

    def run_statement(self, connection: str, statement: str, allow_write: bool = False) -> pa.Table | None:
        return self.pg.run_statement(connection, statement, allow_write=allow_write)

    def session(self, connection: str, read_only: bool) -> _PgSession:
        return _PgSession(connection, read_only)

    def bind_placeholders(self, statement: str) -> tuple[str, list[str]]:
        return self.pg.bind_placeholders(statement)


POSTGRES = _Postgres()
