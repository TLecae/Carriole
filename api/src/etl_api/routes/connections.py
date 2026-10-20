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
