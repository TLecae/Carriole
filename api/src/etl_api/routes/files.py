"""Explorateur de fichiers : parcourt les dossiers autorisés du serveur (voir ADR 0016 et ADR 0018).

Le navigateur n'a pas accès aux fichiers du serveur : l'interface les choisit ici, et le chemin retenu est celui que
voit le worker. Mêmes garde-fous que les modificateurs (`etl_engine.paths`) : rien en dehors des dossiers autorisés.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from etl_engine import formats, paths
from etl_engine.sdk import ConfigError
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(tags=["fichiers"])


@router.get("/files")
def browse(
    path: Annotated[str, Query(description="Dossier à lister ; vide : les dossiers autorisés")] = "",
    mode: Literal["read", "write"] = "read",
    extensions: Annotated[
        str, Query(description="Extensions à montrer, séparées par des virgules (ex. .gpkg,.csv)")
    ] = "",
    recognized: Annotated[bool, Query(description="Ne montrer que les formats que Carriole sait lire")] = False,
) -> dict[str, Any]:
    wanted = tuple(e if e.startswith(".") else f".{e}" for e in (x.strip().lower() for x in extensions.split(",")) if e)
    if recognized:
        wanted = tuple(sorted({*wanted, *formats.EXTENSIONS}))
    if not path.strip():
        return {"path": "", "parent": None, "entries": [], "truncated": False, "roots": paths.browse_roots(mode)}
    try:
        return {**paths.listing(path, mode, wanted), "roots": paths.browse_roots(mode)}
    except ConfigError as exc:
        message = str(exc)
        status = 403 if "accès refusé" in message else 404
        raise HTTPException(status, message) from None
