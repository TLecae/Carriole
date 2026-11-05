from __future__ import annotations

from typing import Annotated, Any

from etl_engine.analysis import analyze
from etl_engine.flow import Flow
from etl_engine.registry import Registry
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from etl_api.deps import get_registry, request_lang

router = APIRouter(tags=["analyse"])


class AnalyzeIn(BaseModel):
    flow: Flow
    params: dict[str, str] = {}


@router.post("/analyze")
def analyze_flow(
    body: AnalyzeIn, registry: Annotated[Registry, Depends(get_registry)], lang: Annotated[str, Depends(request_lang)]
) -> dict[str, Any]:
    """Avertissements avant exécution et schéma de chaque port, sans lire les données (appelé à chaque édition)."""
    return analyze(body.flow, registry, body.params, lang)


class ValuesIn(BaseModel):
    flow: Flow
    node: str
    column: str
    search: str = ""
    limit: int = 50


@router.post("/fields/values")
def field_values(body: ValuesIn, registry: Annotated[Registry, Depends(get_registry)]) -> list[Any]:
    """Valeurs les plus fréquentes d'une colonne du fichier qu'un lecteur lit (aide à la saisie de son filtre)."""
    from etl_engine.field_values import reader_values
    from etl_engine.sdk import ConfigError

    try:
        return reader_values(body.flow, registry, body.node, body.column, body.search, body.limit)
    except ConfigError as exc:
        raise HTTPException(422, str(exc)) from None
