from __future__ import annotations

from typing import Annotated, Any

from etl_engine.analysis import analyze
from etl_engine.flow import Flow
from etl_engine.registry import Registry
from fastapi import APIRouter, Depends
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
