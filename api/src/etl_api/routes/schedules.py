"""Planifications : lancer un flux enregistré selon une expression cron."""

from __future__ import annotations

import re
import time
from typing import Annotated, Any

from etl_engine import drives
from etl_engine.components import embed_components
from etl_engine.flow import Flow, resolve_params
from etl_store import NotFound, Store
from etl_store import access as rights
from etl_store.cron import Cron, CronError
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from etl_api import access
from etl_api.auth import Identity
from etl_api.deps import current_identity, get_store

Who = Annotated[Identity, Depends(current_identity)]

router = APIRouter(tags=["planifications"])


class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    flow_id: str
    cron: str = Field(max_length=100)
    params: dict[str, str] = {}
    cache: bool = False
    notify_email: str | None = None
    allow_overlap: bool = False
    enabled: bool = True

    @field_validator("notify_email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if value and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("adresse e-mail invalide")
        return value or None


class PreviewIn(BaseModel):
    cron: str
    count: int = Field(5, ge=1, le=20)


def _next(cron: str, after: float | None = None) -> float:
    try:
        return Cron.parse(cron).next_after(time.time() if after is None else after)
    except CronError as exc:
        raise HTTPException(422, f"expression cron invalide : {exc}") from None


def _data(body: ScheduleIn, store: Store) -> dict[str, Any]:
    try:
        flow = Flow.model_validate(store.get_flow(body.flow_id)["flow"])
    except NotFound:
        raise HTTPException(422, "flux introuvable") from None
    try:
        resolve_params(flow, body.params)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    embed_components(flow)  # signale tôt un composant introuvable plutôt qu'à la première échéance
    return {
        "name": body.name,
        "flow_id": body.flow_id,
        "cron": body.cron,
        "params": body.params,
        "options": {"cache": body.cache, "notify_email": body.notify_email, "allow_overlap": body.allow_overlap},
        "enabled": body.enabled,
    }


def _decorate(store: Store, schedule: dict[str, Any]) -> dict[str, Any]:
    try:
        record = store.get_flow(schedule["flow_id"])
        schedule["flow_name"] = record["name"]
        schedule["warnings"] = drives.warnings_for(record["flow"])
    except NotFound:
        schedule["flow_name"] = None
        schedule["warnings"] = []
    return schedule


@router.get("/schedules")
def list_schedules(store: Annotated[Store, Depends(get_store)], identity: Who) -> list[dict[str, Any]]:
    """Mode serveur : les siennes (toutes pour un administrateur)."""
    mine = not rights.server_mode() or identity.admin
    return [_decorate(store, s) for s in store.list_schedules() if mine or s.get("owner") == identity.name]


def _own(store: Store, identity: Identity, schedule_id: str) -> dict[str, Any]:
    """Une planification se modifie, se supprime ou se lance par son propriétaire (ou un administrateur)."""
    try:
        sched = store.get_schedule(schedule_id)
    except NotFound:
        raise HTTPException(404, "planification introuvable") from None
    if rights.server_mode() and not identity.admin and sched.get("owner") != identity.name:
        raise HTTPException(404, "planification introuvable")
    return sched


@router.post("/schedules/preview")
def preview(body: PreviewIn) -> dict[str, Any]:
    """Prochaines échéances d'une expression (heure locale du serveur, `ETL_TIMEZONE`)."""
    try:
        return {"next": Cron.parse(body.cron).upcoming(time.time(), body.count)}
    except CronError as exc:
        raise HTTPException(422, f"expression cron invalide : {exc}") from None


@router.post("/schedules", status_code=201)
def create_schedule(
    body: ScheduleIn, store: Annotated[Store, Depends(get_store)], identity: Who, request: Request
) -> dict[str, Any]:
    data = _data(body, store)
    _readable(store, identity, request, data["flow_id"])
    return _decorate(store, store.create_schedule(data, identity.name, _next(body.cron)))


def _readable(store: Store, identity: Identity, request: Request, flow_id: str) -> None:
    """Planifier un flux demande de le lire et de pouvoir utiliser ses connexions (revérifié à chaque échéance)."""
    if rights.server_mode():
        record, _ = access.flow_access(store, identity, flow_id, "read")
        access.require_connections(request, identity, Flow.model_validate(record["flow"]))


@router.put("/schedules/{schedule_id}")
def update_schedule(
    schedule_id: str, body: ScheduleIn, store: Annotated[Store, Depends(get_store)], identity: Who, request: Request
) -> dict[str, Any]:
    _own(store, identity, schedule_id)
    data = _data(body, store)
    _readable(store, identity, request, data["flow_id"])
    try:
        return _decorate(store, store.update_schedule(schedule_id, data, _next(body.cron)))
    except NotFound:
        raise HTTPException(404, "planification introuvable") from None


@router.delete("/schedules/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: str, store: Annotated[Store, Depends(get_store)], identity: Who) -> None:
    _own(store, identity, schedule_id)
    try:
        store.delete_schedule(schedule_id)
    except NotFound:
        raise HTTPException(404, "planification introuvable") from None


@router.post("/schedules/{schedule_id}/run", status_code=201)
def run_now(
    schedule_id: str, store: Annotated[Store, Depends(get_store)], identity: Who, request: Request
) -> dict[str, Any]:
    """Lance tout de suite avec les réglages de la planification (sans toucher à ses échéances)."""
    user = identity.name
    sched = _own(store, identity, schedule_id)
    _readable(store, identity, request, sched["flow_id"])
    try:
        sched = store.get_schedule(schedule_id)
        flow = embed_components(Flow.model_validate(store.get_flow(sched["flow_id"])["flow"]))
    except NotFound:
        raise HTTPException(404, "planification ou flux introuvable") from None
    options = {
        "cache": bool(sched["options"].get("cache", False)),
        "target": None,
        "reuse_from": None,
        "notify_email": sched["options"].get("notify_email"),
        "schedule_id": schedule_id,
    }
    return store.create_run(
        flow.model_dump(mode="json", by_alias=True), sched["flow_id"], sched["params"], options, user
    )
