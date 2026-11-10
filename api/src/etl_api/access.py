"""Contrôle des droits dans les routes (mode serveur, ADR 0054). Ce qu'une personne ne peut pas lire n'existe pas
pour elle (404) ; ce qu'elle lit sans pouvoir le modifier est refusé (403)."""

from __future__ import annotations

import functools
from typing import Annotated, Any

from etl_engine.flow import Flow
from etl_engine.registry import Registry
from etl_store import NotFound, Store
from etl_store import access as rights
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from etl_api.auth import Identity
from etl_api.deps import current_identity


def who(identity: Identity) -> rights.Who:
    return rights.Who(name=identity.name, groups=tuple(identity.groups), admin=identity.admin)


def _deny(granted: str | None, need: str, kind: str) -> None:
    """404 si la personne ne voit pas l'objet ; 403, avec la raison, si son rôle ne suffit pas."""
    if granted is not None and rights.allows(granted, need):
        return
    if kind == "flow":
        if granted is None:
            raise HTTPException(404, "flux introuvable")
        if need == "owner":
            raise HTTPException(403, "seul le propriétaire du flux peut le supprimer ou le partager")
        raise HTTPException(403, "ce flux vous est partagé en lecture : enregistrer une copie à votre nom")
    if kind == "run":
        if granted is None:
            raise HTTPException(404, "run introuvable")
        raise HTTPException(403, "piloter ce run demande de pouvoir modifier son flux")
    if granted is None:
        raise HTTPException(404, "connexion introuvable")
    if need == "owner":
        raise HTTPException(403, "seul le propriétaire de la connexion peut la supprimer ou la partager")
    raise HTTPException(403, "cette connexion vous est partagée en lecture : seul son propriétaire la modifie")


def flow_access(store: Store, identity: Identity, flow_id: str, need: str = "read") -> tuple[dict[str, Any], str]:
    """Le flux et le rôle de la personne ; 404 s'il ne lui est pas visible, 403 s'il faut plus que ce rôle."""
    try:
        record = store.get_flow(flow_id)
    except NotFound:
        raise HTTPException(404, "flux introuvable") from None
    granted = rights.role(store, who(identity), "flow", flow_id, record.get("owner", ""))
    _deny(granted, need, "flow")
    return record, granted or "owner"


def readable_flows(store: Store, identity: Identity) -> dict[str, str] | None:
    """Flux visibles hors des siens : identifiant -> rôle ; `None` : tous (poste de bureau, administrateur)."""
    if not rights.server_mode() or identity.admin:
        return None
    return store.shared_ids("flow", who(identity).principals())


def run_access(store: Store, identity: Identity, run_id: str, need: str = "read") -> dict[str, Any]:
    """Un run est visible de son propriétaire et de ceux qui lisent son flux ; le piloter (arrêt, pause, reprise)
    demande d'écrire dans le flux."""
    try:
        run = store.get_run(run_id, detail=False)
    except NotFound:
        raise HTTPException(404, "run introuvable") from None
    if not rights.server_mode() or identity.admin or run.get("owner") == identity.name:
        return run
    granted = None
    if run.get("flow_id"):
        try:
            owner = store.get_flow(run["flow_id"]).get("owner", "")
        except NotFound:
            owner = ""
        granted = rights.role(store, who(identity), "flow", run["flow_id"], owner)
    _deny(granted, need, "run")
    return run


def connection_access(request: Request, identity: Identity, name: str, need: str = "read") -> str:
    owner = request.app.state.connections.owner(name)
    if owner is None:
        raise HTTPException(404, "connexion introuvable")
    granted = rights.role(request.app.state.store, who(identity), "connection", name, owner)
    _deny(granted, need, "connection")
    return granted or "owner"


def connections_of(flow: Flow, registry: Registry, only: str | None = None) -> set[str]:
    """Connexions nommées dans les paramètres des nœuds (champs « connexion ») ; `only` : un seul nœud."""
    names: set[str] = set()
    for node in flow.nodes:
        if only is not None and node.id != only:
            continue
        if node.type not in registry:
            continue
        for key in _connection_fields(registry.get(node.type)):
            value = (node.params or {}).get(key)
            if isinstance(value, str) and value and "${" not in value:
                names.add(value)
    return names


@functools.cache
def _connection_fields(cls: type) -> tuple[str, ...]:
    properties = cls.Params.model_json_schema().get("properties", {}) if hasattr(cls, "Params") else {}
    return tuple(k for k, spec in properties.items() if spec.get("x-etl-widget") == "connection")


def require_connections(request: Request, identity: Identity, flow: Flow, only: str | None = None) -> None:
    """Toutes les connexions du flux (ou du nœud) doivent être partagées avec la personne (le worker le revérifie)."""
    if not rights.server_mode():
        return
    for name in sorted(connections_of(flow, request.app.state.registry, only)):
        if request.app.state.connections.owner(name) is not None:
            connection_access(request, identity, name, "read")


def readable_run(run: str, request: Request, identity: Annotated[Identity, Depends(current_identity)]) -> None:
    """Dépendance des routes de l'inspecteur et des tuiles (`/data/{run}/…`, `/tiles/{run}/…`) ; mode serveur seulement
    (sur le poste, l'inspecteur lit aussi des runs absents de la base : ligne de commande, essais)."""
    if rights.server_mode():
        run_access(request.app.state.store, identity, run, "read")


ReadableRun = Depends(readable_run)


def readable_connection(name: str, request: Request, identity: Annotated[Identity, Depends(current_identity)]) -> None:
    """Dépendance des routes qui lisent une connexion (`/connections/{name}/…`)."""
    if rights.server_mode():
        connection_access(request, identity, name, "read")


ReadableConnection = Depends(readable_connection)


class SharesIn(BaseModel):
    shares: list[dict[str, Any]] = []


def get_shares(store: Store, kind: str, object_id: str) -> dict[str, Any]:
    return {"shares": store.shares(kind, object_id)}


def put_shares(store: Store, kind: str, object_id: str, body: SharesIn) -> dict[str, Any]:
    try:
        items = rights.clean_shares(body.shares)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    store.set_shares(kind, object_id, items)
    return {"shares": items}


def flow_role(store: Store, identity: Identity, flow_id: str) -> str | None:
    try:
        owner = store.get_flow(flow_id).get("owner", "")
    except NotFound:
        return None
    return rights.role(store, who(identity), "flow", flow_id, owner)
