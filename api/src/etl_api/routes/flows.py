from __future__ import annotations

import json
from typing import Annotated, Any

from etl_engine.flow import Flow
from etl_store import Conflict, DuplicateComponent, FileAccessError, InvalidFlowFile, NotFound, Store
from etl_store import access as rights
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from etl_api import access
from etl_api.auth import Identity
from etl_api.deps import current_identity, current_user, get_store

Who = Annotated[Identity, Depends(current_identity)]

router = APIRouter(tags=["flux"])


class FlowIn(BaseModel):
    flow: Flow
    # révision lue avant modification : 409 si le flux a changé entre-temps (numéro en base, empreinte pour un fichier)
    revision: int | str | None = None
    path: str | None = None  # stockage en fichiers : où créer le flux


class OpenIn(BaseModel):
    path: str


class ReadIn(BaseModel):
    content: str  # texte du fichier déposé dans la page
    name: str = ""  # nom du fichier : messages d'erreur, nom du flux s'il n'en a pas


def _dump(flow: Flow) -> dict[str, Any]:
    return flow.model_dump(mode="json", by_alias=True)


@router.get("/flows")
def list_flows(store: Annotated[Store, Depends(get_store)], identity: Who) -> list[dict[str, Any]]:
    """Base : les flux visibles (mode serveur : les siens, ceux qu'on lui partage, les composants). Fichiers : les
    flux récents. `access` : owner, write ou read."""
    flows = store.list_flows()
    shared = access.readable_flows(store, identity)
    if shared is None:
        return [{**f, "access": "owner"} for f in flows]
    out = []
    for f in flows:
        granted = "owner" if f.get("owner") == identity.name else shared.get(f["id"])
        if granted is None and f.get("component"):  # un composant s'utilise par tous : lisible depuis la palette
            granted = "read"
        if granted:
            out.append({**f, "access": granted})
    return out


@router.get("/examples")
def list_examples() -> list[dict[str, Any]]:
    """Flux d'exemple livrés avec l'application (page d'accueil)."""
    from etl_engine import example_flows

    return example_flows.list_examples()


@router.get("/examples/{example_id}")
def get_example(example_id: str) -> dict[str, Any]:
    """Le flux d'un exemple : l'éditeur l'ouvre comme un flux neuf, à enregistrer où l'on veut."""
    from etl_engine import example_flows

    flow = example_flows.load_example(example_id)
    if flow is None:
        raise HTTPException(404, "exemple introuvable")
    return flow


@router.post("/flows/open")
def open_flow(body: OpenIn, store: Annotated[Store, Depends(get_store)]):
    """Ouvre un fichier de flux par son chemin (stockage en fichiers)."""
    try:
        return store.open_flow_file(body.path)
    except NotImplementedError as exc:
        raise HTTPException(501, str(exc)) from None
    except NotFound:
        raise HTTPException(404, f"fichier introuvable : {body.path}") from None
    except InvalidFlowFile as exc:
        raise HTTPException(422, str(exc)) from None
    except FileAccessError as exc:
        raise HTTPException(503, f"fichier inaccessible : {exc}") from None


@router.post("/flows/read")
def read_flow(body: ReadIn) -> dict[str, Any]:
    """Lit un flux reçu en texte (fichier glissé dans la page), sans l'enregistrer : l'éditeur l'ouvre comme un flux
    neuf. Mêmes règles qu'un fichier ouvert : anciens noms de modificateurs, clés absentes complétées."""
    name = body.name.strip() or "flux.json"
    try:
        content = json.loads(body.content.removeprefix("\ufeff"))
        if not isinstance(content, dict):
            raise ValueError("un objet JSON est attendu")
        doc = _dump(Flow.model_validate(content))
    except ValueError as exc:
        raise HTTPException(422, f"{name} n'est pas un flux lisible : {exc}") from None
    if not doc.get("name"):
        doc["name"] = name[:-5] if name.lower().endswith(".json") else name
    return doc


@router.post("/flows", status_code=201)
def create_flow(body: FlowIn, store: Annotated[Store, Depends(get_store)], user: Annotated[str, Depends(current_user)]):
    try:
        return store.create_flow(body.flow.name or "(sans nom)", _dump(body.flow), user, path=body.path)
    except DuplicateComponent as exc:
        raise HTTPException(422, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from None
    except FileAccessError as exc:
        raise HTTPException(503, f"enregistrement impossible : {exc}") from None


@router.get("/flows/{flow_id}")
def get_flow(flow_id: str, store: Annotated[Store, Depends(get_store)], identity: Who):
    if rights.server_mode():
        if _component(store, flow_id):  # un composant se lit par tous (palette) ; le modifier suit ses partages
            return {**store.get_flow(flow_id), "access": access.flow_role(store, identity, flow_id) or "read"}
        record, granted = access.flow_access(store, identity, flow_id, "read")
        return {**record, "access": granted}
    try:
        return store.get_flow(flow_id)
    except NotFound:
        raise HTTPException(404, "flux introuvable") from None
    except InvalidFlowFile as exc:
        raise HTTPException(422, str(exc)) from None
    except FileAccessError as exc:
        raise HTTPException(503, f"fichier inaccessible : {exc}") from None


@router.put("/flows/{flow_id}")
def update_flow(flow_id: str, body: FlowIn, store: Annotated[Store, Depends(get_store)], identity: Who):
    if rights.server_mode():
        access.flow_access(store, identity, flow_id, "write")
    try:
        return store.update_flow(flow_id, body.flow.name or "(sans nom)", _dump(body.flow), body.revision)
    except NotFound:
        raise HTTPException(404, "flux introuvable") from None
    except DuplicateComponent as exc:
        raise HTTPException(422, str(exc)) from None
    except Conflict as exc:
        raise HTTPException(409, f"le flux a été modifié par quelqu'un d'autre ({exc})") from None
    except FileAccessError as exc:
        raise HTTPException(503, f"enregistrement impossible : {exc}") from None


@router.delete("/flows/{flow_id}", status_code=204)
def delete_flow(flow_id: str, store: Annotated[Store, Depends(get_store)], identity: Who) -> None:
    """Base : supprime le flux. Fichiers : le retire des récents (le fichier n'est jamais supprimé)."""
    if rights.server_mode():
        access.flow_access(store, identity, flow_id, "owner")
    try:
        store.delete_flow(flow_id)
    except NotFound:
        raise HTTPException(404, "flux introuvable") from None


def _component(store: Store, flow_id: str) -> bool:
    try:
        return bool(store.get_flow(flow_id).get("component"))
    except NotFound:
        return False


@router.get("/flows/{flow_id}/shares")
def flow_shares(flow_id: str, store: Annotated[Store, Depends(get_store)], identity: Who) -> dict[str, Any]:
    """Partages d'un flux : visibles de qui le lit, modifiables par son propriétaire (ADR 0054)."""
    record, granted = access.flow_access(store, identity, flow_id, "read")
    return {**access.get_shares(store, "flow", flow_id), "owner": record.get("owner", ""), "access": granted}


@router.put("/flows/{flow_id}/shares")
def set_flow_shares(
    flow_id: str, body: access.SharesIn, store: Annotated[Store, Depends(get_store)], identity: Who
) -> dict[str, Any]:
    access.flow_access(store, identity, flow_id, "owner")
    return access.put_shares(store, "flow", flow_id, body)
