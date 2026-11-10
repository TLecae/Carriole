"""Connexions nommées aux bases (PostgreSQL, Oracle). Le mot de passe est reçu, jamais renvoyé."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from etl_engine.formats import databases
from etl_engine.sdk import ConfigError
from etl_store import ConnectionStore, InvalidConnection, NotFound
from etl_store import access as rights
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from etl_api import access
from etl_api.access import ReadableConnection
from etl_api.auth import Identity
from etl_api.deps import current_identity

Who = Annotated[Identity, Depends(current_identity)]

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
def list_connections(conns: Conns, request: Request, identity: Who) -> list[dict[str, Any]]:
    """Mode serveur : ses connexions et celles qu'on lui partage, avec `access` (owner, write, read)."""
    items = conns.list()
    if not rights.server_mode() or identity.admin:
        return [{**c, "access": "owner"} for c in items]
    shared = request.app.state.store.shared_ids("connection", access.who(identity).principals())
    out = []
    for c in items:
        granted = "owner" if c.get("owner") == identity.name else shared.get(c["name"])
        if granted:
            out.append({**c, "access": granted})
    return out


@router.put("/connections/{name}")
def save_connection(name: str, body: ConnectionIn, conns: Conns, request: Request, identity: Who):
    if rights.server_mode() and conns.owner(name) is not None:  # une connexion existante : il faut pouvoir l'écrire
        access.connection_access(request, identity, name, "write")
    try:
        return conns.save(name, body.model_dump(exclude={"password"}), body.password, identity.name)
    except InvalidConnection as exc:
        raise HTTPException(422, str(exc)) from None


@router.delete("/connections/{name}", status_code=204)
def delete_connection(name: str, conns: Conns, request: Request, identity: Who) -> None:
    if rights.server_mode():
        access.connection_access(request, identity, name, "owner")
    try:
        conns.delete(name)
    except NotFound:
        raise HTTPException(404, "connexion introuvable") from None


@router.post("/connections/{name}/test", dependencies=[ReadableConnection])
async def test_connection(name: str, conns: Conns) -> dict[str, Any]:
    try:
        conns.get(name)
    except NotFound:
        raise HTTPException(404, "connexion introuvable") from None
    try:
        return {"ok": True, **await asyncio.to_thread(lambda: databases.backend(name).check_connection(name))}
    except ConfigError as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/connections/{name}/tables", dependencies=[ReadableConnection])
async def tables(name: str, conns: Conns) -> list[dict[str, Any]]:
    try:
        conns.get(name)
    except NotFound:
        raise HTTPException(404, "connexion introuvable") from None
    try:
        return await asyncio.to_thread(lambda: databases.backend(name).list_tables(name))
    except ConfigError as exc:
        raise HTTPException(502, str(exc)) from None


@router.get("/connections/{name}/schema", dependencies=[ReadableConnection])
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


@router.get("/connections/{name}/values", dependencies=[ReadableConnection])
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
async def check_sql(body: SqlCheck, conns: Conns, request: Request, identity: Who) -> dict[str, Any]:
    if body.connection and rights.server_mode() and conns.owner(body.connection) is not None:
        access.connection_access(request, identity, body.connection, "read")
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


@router.get("/connections/{name}/columns", dependencies=[ReadableConnection])
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


@router.get("/connections/{name}/shares", dependencies=[ReadableConnection])
def connection_shares(name: str, request: Request, identity: Who) -> dict[str, Any]:
    granted = access.connection_access(request, identity, name, "read")
    store = request.app.state.store
    return {
        **access.get_shares(store, "connection", name),
        "owner": request.app.state.connections.owner(name),
        "access": granted,
    }


@router.put("/connections/{name}/shares")
def set_connection_shares(name: str, body: access.SharesIn, request: Request, identity: Who) -> dict[str, Any]:
    access.connection_access(request, identity, name, "owner")
    return access.put_shares(request.app.state.store, "connection", name, body)
