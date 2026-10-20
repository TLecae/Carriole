from __future__ import annotations

from typing import Annotated, Any

from etl_engine.flow import Flow
from etl_store import Conflict, DuplicateComponent, FileAccessError, InvalidFlowFile, NotFound, Store
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from etl_api.deps import current_user, get_store

router = APIRouter(tags=["flux"])


class FlowIn(BaseModel):
    flow: Flow
    # révision lue avant modification : 409 si le flux a changé entre-temps (numéro en base, empreinte pour un fichier)
    revision: int | str | None = None
    path: str | None = None  # stockage en fichiers : où créer le flux


class OpenIn(BaseModel):
    path: str


def _dump(flow: Flow) -> dict[str, Any]:
    return flow.model_dump(mode="json", by_alias=True)


@router.get("/flows")
def list_flows(store: Annotated[Store, Depends(get_store)]) -> list[dict[str, Any]]:
    """Base : tous les flux. Fichiers : les flux récents."""
    return store.list_flows()


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
def get_flow(flow_id: str, store: Annotated[Store, Depends(get_store)]):
    try:
        return store.get_flow(flow_id)
    except NotFound:
        raise HTTPException(404, "flux introuvable") from None
    except InvalidFlowFile as exc:
        raise HTTPException(422, str(exc)) from None
    except FileAccessError as exc:
        raise HTTPException(503, f"fichier inaccessible : {exc}") from None


@router.put("/flows/{flow_id}")
def update_flow(flow_id: str, body: FlowIn, store: Annotated[Store, Depends(get_store)]):
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
def delete_flow(flow_id: str, store: Annotated[Store, Depends(get_store)]) -> None:
    """Base : supprime le flux. Fichiers : le retire des récents (le fichier n'est jamais supprimé)."""
    try:
        store.delete_flow(flow_id)
    except NotFound:
        raise HTTPException(404, "flux introuvable") from None
