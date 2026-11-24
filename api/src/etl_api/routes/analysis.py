from __future__ import annotations

from typing import Annotated, Any

import pyarrow as pa
from etl_engine.analysis import RanSchema, analyze
from etl_engine.cache import port_schema
from etl_engine.flow import Flow
from etl_engine.registry import Registry
from etl_store import access as rights
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from etl_api import access
from etl_api.auth import Identity
from etl_api.data_service import SAFE_ID
from etl_api.deps import current_identity, get_registry, request_lang

router = APIRouter(tags=["analyse"])


class AnalyzeIn(BaseModel):
    flow: Flow
    params: dict[str, str] = {}
    run: str = ""  # dernier run du flux : schémas des ports qui ne connaissent le leur qu'à l'exécution


def _ran_schemas(request: Request, identity: Identity, run: str) -> RanSchema | None:
    """Schéma des données d'un port au run `run` (cache), s'il est lisible par l'utilisateur."""
    duck = getattr(request.app.state, "duck", None)
    if not run or duck is None or not SAFE_ID.match(run):
        return None
    if rights.server_mode():
        try:
            access.run_access(request.app.state.store, identity, run, "read")
        except HTTPException:
            return None
    run_dir = duck.root / run

    def ran(node: str, port: str) -> pa.Schema | None:
        if not (SAFE_ID.match(node) and SAFE_ID.match(port)):
            return None
        return port_schema(run_dir, node, port)

    return ran if run_dir.is_dir() else None


@router.post("/analyze")
def analyze_flow(
    body: AnalyzeIn,
    registry: Annotated[Registry, Depends(get_registry)],
    lang: Annotated[str, Depends(request_lang)],
    request: Request,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, Any]:
    """Avertissements avant exécution et schéma de chaque port, sans lire les données (appelé à chaque édition)."""
    return analyze(body.flow, registry, body.params, lang, _ran_schemas(request, identity, body.run))


class ValuesIn(BaseModel):
    flow: Flow
    node: str
    column: str
    search: str = ""
    limit: int = 50


@router.post("/fields/values")
def field_values(
    body: ValuesIn,
    registry: Annotated[Registry, Depends(get_registry)],
    request: Request,
    identity: Annotated[Identity, Depends(current_identity)],
) -> list[Any]:
    """Valeurs les plus fréquentes d'une colonne du fichier qu'un lecteur lit (aide à la saisie de son filtre)."""
    access.require_connections(request, identity, body.flow, only=body.node)
    from etl_engine.field_values import reader_values
    from etl_engine.sdk import ConfigError

    try:
        return reader_values(body.flow, registry, body.node, body.column, body.search, body.limit)
    except ConfigError as exc:
        raise HTTPException(422, str(exc)) from None


class ReaderIn(BaseModel):
    flow: Flow
    node: str


@router.post("/fields/columns")
def field_columns(
    body: ReaderIn,
    registry: Annotated[Registry, Depends(get_registry)],
    request: Request,
    identity: Annotated[Identity, Depends(current_identity)],
) -> list[dict[str, str]]:
    """Colonnes du fichier qu'un lecteur lit, avant le choix de ses attributs (requête de lecture)."""
    access.require_connections(request, identity, body.flow, only=body.node)
    from etl_engine.field_values import reader_columns
    from etl_engine.sdk import ConfigError

    try:
        return reader_columns(body.flow, registry, body.node)
    except ConfigError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/fields/target")
def field_target(
    body: ReaderIn,
    registry: Annotated[Registry, Depends(get_registry)],
    request: Request,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, Any]:
    """Écriture en ajout dans un fichier : colonnes de la couche visée, face aux attributs que le nœud reçoit."""
    access.require_connections(request, identity, body.flow, only=body.node)
    from etl_engine.field_values import writer_target
    from etl_engine.sdk import ConfigError

    try:
        return writer_target(body.flow, registry, body.node)
    except ConfigError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/fields/count")
def field_count(
    body: ReaderIn,
    registry: Annotated[Registry, Depends(get_registry)],
    request: Request,
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, int]:
    """Entités du fichier qu'un lecteur lit, et celles que son filtre garde."""
    access.require_connections(request, identity, body.flow, only=body.node)
    from etl_engine.field_values import reader_count
    from etl_engine.sdk import ConfigError

    try:
        return reader_count(body.flow, registry, body.node)
    except ConfigError as exc:
        raise HTTPException(422, str(exc)) from None
