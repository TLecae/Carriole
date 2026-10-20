from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from etl_engine import batch
from etl_engine.analysis import analyze
from etl_engine.components import embed_components
from etl_engine.flow import Flow, resolve_params
from etl_engine.registry import Registry
from etl_store import FINAL_STATUSES, NotFound, Store
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator

from etl_api.deps import current_user, get_registry, get_store, request_lang

router = APIRouter(tags=["runs"])


class RunIn(BaseModel):
    flow_id: str | None = None
    flow: Flow | None = None  # brouillon non enregistré ; sinon le flux `flow_id`
    params: dict[str, str] = {}
    cache: bool = True
    target: str | None = None  # « lancer jusqu'à ce nœud »
    reuse_from: str | None = None  # run dont on réutilise les nœuds inchangés (« relancer à partir d'un nœud »)
    notify_email: str | None = None  # adresse à prévenir à la fin du run (si le serveur a un SMTP)

    @field_validator("notify_email")
    @classmethod
    def _email(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        if v and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            raise ValueError("adresse e-mail invalide")
        return v or None


class RetryIn(BaseModel):
    resume: bool = False  # vrai : repart du cache du run d'origine (les nœuds terminés ne sont pas recalculés)


def _run_or_404(store: Store, run_id: str, detail: bool = True) -> dict[str, Any]:
    try:
        return store.get_run(run_id, detail)
    except NotFound:
        raise HTTPException(404, "run introuvable") from None


def _create(
    body: RunIn,
    store: Store,
    registry: Registry,
    user: str,
    lang: str,
    extra_options: dict[str, Any] | None = None,
    prepared: tuple[Flow, str | None] | None = None,
) -> dict[str, Any]:
    """Valide puis met en file un run. `prepared` : flux et chemin déjà chargés (lots : un seul chargement)."""
    flow, flow_path = prepared if prepared else _load_flow(body, store)
    try:
        resolve_params(flow, body.params)
    except ValueError as exc:
        raise HTTPException(422, {"message": str(exc), "issues": []}) from None
    issues = [i for i in analyze(flow, registry, body.params, lang)["issues"] if i["level"] == "error"]
    if issues:
        raise HTTPException(422, {"message": "le flux contient des erreurs", "issues": issues})
    if body.target and body.target not in {n.id for n in flow.nodes}:
        raise HTTPException(422, "nœud cible inconnu")
    if body.reuse_from:
        _run_or_404(store, body.reuse_from, detail=False)
    options = {
        "cache": body.cache,
        "target": body.target,
        "reuse_from": body.reuse_from,
        "notify_email": body.notify_email,
        "locale": lang,  # langue du journal et des erreurs du run
        "flow_path": flow_path,
        **(extra_options or {}),
    }
    flow = embed_components(flow)  # le run garde la version des composants utilisée : relançable à l'identique
    return store.create_run(flow.model_dump(mode="json", by_alias=True), body.flow_id, body.params, options, user)


def _load_flow(body: RunIn, store: Store) -> tuple[Flow, str | None]:
    flow = body.flow
    flow_path = None
    if body.flow_id:  # flux en fichier : son dossier sert à ${sys:flow_dir}
        try:
            record = store.get_flow(body.flow_id)
            flow_path = record.get("path")
            if flow is None:
                flow = Flow.model_validate(record["flow"])
        except NotFound:
            if flow is None:
                raise HTTPException(404, "flux introuvable") from None
        except Exception:  # noqa: BLE001 - le brouillon envoyé suffit : seul le dossier du flux est perdu
            if flow is None:
                raise
    if flow is None:
        raise HTTPException(422, "flow ou flow_id requis")
    return flow, flow_path


@router.post("/runs", status_code=201)
def create_run(
    body: RunIn,
    store: Annotated[Store, Depends(get_store)],
    registry: Annotated[Registry, Depends(get_registry)],
    user: Annotated[str, Depends(current_user)],
    lang: Annotated[str, Depends(request_lang)],
) -> dict[str, Any]:
    """Crée un job : un worker l'exécutera, indépendamment du navigateur."""
    return _create(body, store, registry, user, lang)


MAX_BATCH = 500


class BatchIn(RunIn):
    param: str = ""  # paramètre publié qui reçoit chaque valeur
    pattern: str | None = None  # motif de fichiers (côté serveur) ; sinon `values`
    values: list[str] = []
    dry_run: bool = False  # seulement lister les valeurs, sans rien lancer


@router.post("/runs/batch", status_code=201)
def create_batch(
    body: BatchIn,
    store: Annotated[Store, Depends(get_store)],
    registry: Annotated[Registry, Depends(get_registry)],
    user: Annotated[str, Depends(current_user)],
    lang: Annotated[str, Depends(request_lang)],
) -> dict[str, Any]:
    """Lot : le même flux, un run par fichier du motif (ou par valeur), regroupés par `options.batch`."""
    flow, flow_path = _load_flow(body, store)
    if body.param not in {p.name for p in flow.published_params}:
        raise HTTPException(422, f"paramètre publié inconnu : {body.param}")
    if body.pattern:
        try:
            values = batch.expand_each(body.pattern)
        except Exception as exc:  # noqa: BLE001 - motif invalide ou dossier refusé : message pour l'utilisateur
            raise HTTPException(422, f"motif illisible : {exc}") from None
    else:
        values = [v.strip() for v in body.values if v.strip()]
    if not values:
        raise HTTPException(422, "le lot est vide : aucun fichier ni aucune valeur")
    if len(values) > MAX_BATCH:
        raise HTTPException(422, f"lot trop grand : {len(values)} runs (maximum {MAX_BATCH})")
    if body.dry_run:
        return {"values": values}
    batch_id = uuid.uuid4().hex
    runs = []
    for index, value in enumerate(values, 1):
        item = body.model_copy(update={"params": {**body.params, body.param: value}})
        info = {"id": batch_id, "index": index, "size": len(values), "label": os.path.basename(value) or value}
        runs.append(_create(item, store, registry, user, lang, {"batch": info}, (flow, flow_path)))
    return {"batch": batch_id, "runs": runs}


@router.post("/runs/{run_id}/retry", status_code=201)
def retry_run(
    run_id: str,
    body: RetryIn,
    store: Annotated[Store, Depends(get_store)],
    user: Annotated[str, Depends(current_user)],
) -> dict[str, Any]:
    """Relance un run avec le flux **tel qu'il a été exécuté** et les mêmes paramètres.

    `resume` : repart de son cache. Utile après une interruption ou un échec : seuls les nœuds non terminés
    sont recalculés.
    """
    old = _run_or_404(store, run_id)
    if old["status"] in ("queued", "running"):
        raise HTTPException(409, "ce run n'est pas terminé")
    options = {**old["options"], "cache": True, "target": None, "reuse_from": run_id if body.resume else None}
    return store.create_run(old["flow"], old["flow_id"], old["params"], options, user)


@router.get("/runs")
def list_runs(
    store: Annotated[Store, Depends(get_store)],
    status: str | None = Query(None, description="un ou plusieurs statuts séparés par des virgules"),
    mine: bool = False,
    flow_id: str | None = None,
    user: Annotated[str, Depends(current_user)] = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    return store.list_runs(status=status, owner=user if mine else None, flow_id=flow_id, limit=min(limit, 500))


@router.get("/runs/{run_id}")
def get_run(run_id: str, store: Annotated[Store, Depends(get_store)]) -> dict[str, Any]:
    run = _run_or_404(store, run_id)
    run["counts"] = store.last_counts(run_id)  # pour repeindre les connexions à la reconnexion
    return run


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, store: Annotated[Store, Depends(get_store)]) -> dict[str, Any]:
    try:
        return store.request_cancel(run_id)
    except NotFound:
        raise HTTPException(404, "run introuvable") from None


@router.get("/runs/{run_id}/logs")
def run_logs(
    run_id: str,
    store: Annotated[Store, Depends(get_store)],
    node: str | None = None,
    level: str | None = None,
    after: int = 0,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    _run_or_404(store, run_id, detail=False)
    wanted = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
    floor = wanted.get((level or "").upper(), 0)
    logs = store.events_after(run_id, after, limit, types=("log",))
    return [
        e for e in logs if (node is None or e.get("node") == node) and wanted.get(e.get("level", "INFO"), 20) >= floor
    ]


@router.get("/runs/{run_id}/events")
async def run_events(
    run_id: str,
    request: Request,
    store: Annotated[Store, Depends(get_store)],
    after: int = 0,
    last_event_id: Annotated[str | None, Header()] = None,
    stream: bool = True,
):
    """Événements du run. SSE avec reprise : un client qui se reconnecte renvoie `Last-Event-ID`
    (ou `?after=`) et ne reçoit que la suite. `?stream=false` renvoie la liste JSON.
    """
    await asyncio.to_thread(_run_or_404, store, run_id, False)
    # À la reconnexion, le navigateur renvoie `Last-Event-ID` (à jour) avec le `after` initial (périmé) :
    # le plus grand gagne.
    start = max(after, int(last_event_id) if last_event_id and last_event_id.isdigit() else 0)
    if not stream:
        return await asyncio.to_thread(store.events_after, run_id, start, 5000)

    async def gen() -> AsyncIterator[str]:
        last, idle = start, 0
        while True:
            events = await asyncio.to_thread(store.events_after, run_id, last, 500)
            for e in events:
                last = e["id"]
                yield f"id: {e['id']}\nevent: {e['type']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n"
            if events:
                idle = 0
                continue
            run = await asyncio.to_thread(store.get_run, run_id, False)
            if run["status"] in FINAL_STATUSES:
                yield f"event: end\ndata: {json.dumps({'status': run['status'], 'error': run['error']})}\n\n"
                return
            if await request.is_disconnected():
                return
            idle += 1
            if idle % 40 == 0:
                yield ": keepalive\n\n"
            await asyncio.sleep(0.3)

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@router.get("/disk")
def disk_usage(request: Request, store: Annotated[Store, Depends(get_store)]) -> dict[str, Any]:
    """Place prise par le cache et limites de rétention (la purge est faite par les workers)."""
    from etl_store.purge import dir_size

    settings = request.app.state.settings
    cache = settings.cache_dir
    used = dir_size(cache) if cache.is_dir() else 0
    return {
        "cache_bytes": used,
        "max_bytes": settings.cache_max_bytes,
        "retention_days": settings.cache_retention_days,
        "runs_with_data": len(store.runs_for_purge()),
    }


class HoldIn(BaseModel):
    page: str
    runs: list[str] = []


@router.post("/cache/hold")
def hold_cache(body: HoldIn, request: Request) -> dict[str, Any]:
    """Une page signale les runs qu'elle affiche : leur cache est gardé (poste de bureau, voir `etl_api.cache_hold`)."""
    request.app.state.holds.hold(body.page, body.runs)
    return {"policy": request.app.state.settings.cache_policy}


@router.post("/cache/release")
async def release_cache(request: Request) -> dict[str, str]:
    """Page fermée : ses runs ne sont plus gardés. Envoyé par `navigator.sendBeacon` à la fermeture, en texte brut
    (un type JSON n'y est pas toujours permis) : le corps est lu tel quel."""
    try:
        page = str(json.loads(await request.body())["page"])
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status_code=422, detail="page de l'interface manquante") from None
    request.app.state.holds.release(page)
    return {"status": "ok"}
