"""Connexions nommées aux bases (PostgreSQL, Oracle). Le mot de passe est reçu, jamais renvoyé."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from etl_engine.formats import databases
from etl_engine.sdk import ConfigError
from etl_store import ConnectionStore, InvalidConnection, NotFound
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from etl_api.deps import current_user

router = APIRouter(tags=["connexions"])


def get_connections(request: Request) -> ConnectionStore:
    return request.app.state.connections


Conns = Annotated[ConnectionStore, Depends(get_connections)]


class ConnectionIn(BaseModel):
    kind: str = "postgres"
    host: str
    port: int | None = None  # absent : celui du type de base
    dbname: str
    user: str = ""
    sslmode: str = "prefer"
    options: dict[str, Any] = {}
    password: str | None = Field(default=None, description="Absent : conserver le mot de passe actuel.")


@router.get("/connections")
def list_connections(conns: Conns) -> list[dict[str, Any]]:
    return conns.list()


@router.put("/connections/{name}")
def save_connection(name: str, body: ConnectionIn, conns: Conns, user: Annotated[str, Depends(current_user)]):
    try:
        return conns.save(name, body.model_dump(exclude={"password"}), body.password, user)
    except InvalidConnection as exc:
        raise HTTPException(422, str(exc)) from None


@router.delete("/connections/{name}", status_code=204)
def delete_connection(name: str, conns: Conns) -> None:
    try:
        conns.delete(name)
    except NotFound:
        raise HTTPException(404, "connexion introuvable") from None


@router.post("/connections/{name}/test")
async def test_connection(name: str, conns: Conns) -> dict[str, Any]:
    try:
        conns.get(name)
    except NotFound:
        raise HTTPException(404, "connexion introuvable") from None
    try:
        return {"ok": True, **await asyncio.to_thread(lambda: databases.backend(name).check_connection(name))}
    except ConfigError as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/connections/{name}/tables")
async def tables(name: str, conns: Conns) -> list[dict[str, Any]]:
    try:
        conns.get(name)
    except NotFound:
        raise HTTPException(404, "connexion introuvable") from None
    try:
        return await asyncio.to_thread(lambda: databases.backend(name).list_tables(name))
    except ConfigError as exc:
        raise HTTPException(502, str(exc)) from None


@router.get("/connections/{name}/schema")
async def schema(name: str, conns: Conns) -> dict[str, Any]:
    """Tables et colonnes de la base, et son dialecte : complétion de l'éditeur SQL."""
    try:
        conns.get(name)
    except NotFound:
        raise HTTPException(404, "connexion introuvable") from None
    try:
        db = databases.backend(name)
        tables = await asyncio.to_thread(lambda: db.schema_columns(name))
    except ConfigError as exc:
        raise HTTPException(502, str(exc)) from None
    return {"dialect": db.sql_dialect, "tables": tables}


@router.get("/connections/{name}/values")
async def values(name: str, table: str, column: str, conns: Conns, search: str = "", limit: int = 50) -> list[Any]:
    """Valeurs les plus fréquentes d'une colonne : l'éditeur SQL les propose à l'insertion."""
    try:
        conns.get(name)
    except NotFound:
        raise HTTPException(404, "connexion introuvable") from None
    try:
        db = databases.backend(name)
        return await asyncio.to_thread(lambda: db.column_values(name, table, column, search, limit))
    except ConfigError as exc:
        raise HTTPException(502, str(exc)) from None


class SqlCheck(BaseModel):
    sql: str = Field(max_length=200_000)
    connection: str = ""  # vide : SQL de DuckDB


@router.post("/sql/check")
async def check_sql(body: SqlCheck, conns: Conns) -> dict[str, Any]:
    """Vérifie un SQL sans l'exécuter (syntaxe DuckDB, ou plan de la base de la connexion)."""
    from etl_engine import sql_check

    if not body.connection:
        return await asyncio.to_thread(lambda: sql_check.check_duckdb(body.sql))
    try:
        conns.get(body.connection)
    except NotFound:
        raise HTTPException(404, "connexion introuvable") from None
    try:
        db = databases.backend(body.connection)
        return await asyncio.to_thread(lambda: db.check_sql(body.connection, body.sql))
    except ConfigError as exc:
        raise HTTPException(502, str(exc)) from None


@router.get("/connections/{name}/columns")
async def columns(name: str, table: str, conns: Conns) -> dict[str, Any]:
    """Colonnes d'une table où écrire : l'éditeur montre en rouge les attributs qu'elle ne peut pas recevoir."""
    try:
        conns.get(name)
    except NotFound:
        raise HTTPException(404, "connexion introuvable") from None
    try:
        db = databases.backend(name)
        found = await asyncio.to_thread(lambda: db.table_columns(name, table))
    except ConfigError as exc:
        raise HTTPException(502, str(exc)) from None
    return {"exists": found is not None, "columns": found or [], "case_sensitive": db.case_sensitive_columns}
