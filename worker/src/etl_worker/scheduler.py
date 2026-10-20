"""Planificateur : crée un run pour chaque planification arrivée à échéance.

Il tourne dans un thread de chaque worker ; un verrou périodique en base (`try_acquire`) garantit qu'un seul d'entre
eux fait le travail à chaque tick. Il ne fait que **créer des runs en attente** : ce sont les workers qui les exécutent,
comme n'importe quel autre run (concurrence, reprise, notification, purge identiques).

Règles :
- une échéance manquée de plus de `grace` secondes (workers arrêtés) est ignorée, pas rattrapée ;
- si le run précédent de la planification n'est pas terminé, l'échéance est ignorée (sauf `allow_overlap`) ;
- une erreur (flux supprimé, paramètre invalide…) est consignée dans `last_error` et la planification continue.
"""

from __future__ import annotations

import logging
import threading
import time

from etl_engine.components import embed_components
from etl_engine.flow import Flow, resolve_params
from etl_store import NotFound, Store
from etl_store.cron import Cron, CronError

log = logging.getLogger("etl.scheduler")

TICK_INTERVAL = 10.0
GRACE_SECONDS = 3600.0


def tick(store: Store, now: float | None = None, grace: float = GRACE_SECONDS) -> list[str]:
    """Traite les planifications échues ; renvoie les identifiants des runs créés."""
    now = time.time() if now is None else now
    created: list[str] = []
    for sched in store.due_schedules(now):
        try:
            following = Cron.parse(sched["cron"]).next_after(now)
        except CronError as exc:
            store.mark_schedule_fired(sched["id"], None, now, None, f"expression cron invalide : {exc}")
            continue
        late = now - (sched["next_run_at"] or now)
        if late > grace:
            message = f"échéance manquée de {int(late // 60)} min, ignorée"
            store.mark_schedule_fired(sched["id"], None, now, following, message)
            log.warning("planification %s : %s", sched["name"], message)
            continue
        if _still_running(store, sched) and not sched["options"].get("allow_overlap"):
            store.mark_schedule_fired(
                sched["id"], None, now, following, "run précédent encore en cours : échéance ignorée"
            )
            continue
        try:
            flow = Flow.model_validate(store.get_flow(sched["flow_id"])["flow"])
            resolve_params(flow, sched["params"])
            flow = embed_components(flow)
            options = {
                "cache": bool(sched["options"].get("cache", False)),
                "target": None,
                "reuse_from": None,
                "notify_email": sched["options"].get("notify_email") or None,
                "schedule_id": sched["id"],
            }
            run = store.create_run(
                flow.model_dump(mode="json", by_alias=True), sched["flow_id"], sched["params"], options, sched["owner"]
            )
        except NotFound:
            store.mark_schedule_fired(sched["id"], None, now, following, "le flux planifié n'existe plus")
            continue
        except ValueError as exc:
            store.mark_schedule_fired(sched["id"], None, now, following, f"paramètres invalides : {exc}")
            continue
        store.mark_schedule_fired(sched["id"], run["id"], now, following, None)
        created.append(run["id"])
        log.info("planification %s : run %s créé", sched["name"], run["id"])
    return created


def _still_running(store: Store, sched: dict) -> bool:
    last = sched.get("last_run_id")
    if not last:
        return False
    try:
        return store.get_run(last, detail=False)["status"] in ("queued", "running")
    except NotFound:
        return False


def loop(store: Store, shutdown: threading.Event, interval: float = TICK_INTERVAL) -> None:
    while not shutdown.is_set():
        try:
            if store.try_acquire("scheduler", interval * 0.9):
                tick(store)
        except Exception:  # noqa: BLE001 - le planificateur ne doit jamais s'arrêter sur une erreur ponctuelle
            log.exception("erreur du planificateur")
        shutdown.wait(interval)
