"""Versions publiées des composants (voir ADR 0014 et 0017)."""

from __future__ import annotations

import json
from typing import Annotated, Any

from etl_engine.components import embed_components
from etl_engine.flow import Flow
from etl_store import NothingToPublish, Store
from etl_store import access as rights
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from etl_api import access
from etl_api.auth import Identity
from etl_api.deps import current_identity, get_store

router = APIRouter(tags=["composants"])


class PublishIn(BaseModel):
    note: str = ""


def _snapshot(store: Store, name: str) -> dict[str, Any]:
    """Le composant tel qu'enregistré, ses composants imbriqués épinglés à leur dernière version."""
    row = store.get_component(name)
    if row is None:
        raise HTTPException(404, "composant introuvable (enregistrer d'abord le flux comme composant)")
    return embed_components(Flow.model_validate(row["flow"])).model_dump(mode="json", by_alias=True)


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)


@router.get("/components/{name}/versions")
def list_versions(name: str, store: Annotated[Store, Depends(get_store)]) -> dict[str, Any]:
    """Versions publiées (la plus récente d'abord) et `draft_changed` : le flux diffère de la dernière version."""
    versions = store.list_component_versions(name)
    draft_changed = True
    if versions and store.get_component(name) is not None:
        latest = store.get_component_version(name)
        draft_changed = latest is None or not _same(latest["flow"], _snapshot(store, name))
    return {"versions": versions, "draft_changed": draft_changed}


@router.post("/components/{name}/versions", status_code=201)
def publish_version(
    name: str,
    body: PublishIn,
    store: Annotated[Store, Depends(get_store)],
    identity: Annotated[Identity, Depends(current_identity)],
) -> dict[str, Any]:
    """Publie le composant **enregistré** comme nouvelle version immuable ; en mode serveur, il faut pouvoir
    modifier son flux."""
    user = identity.name
    row = store.get_component(name)
    if row is not None and rights.server_mode():
        access.flow_access(store, identity, row["id"], "write")
    try:
        return store.publish_component(name, _snapshot(store, name), body.note.strip(), user)
    except NothingToPublish as exc:
        raise HTTPException(409, str(exc)) from None
