"""Exécution d'un run réservé par un worker : événements en base, annulation, arrêt propre."""

from __future__ import annotations

import logging
import threading
import traceback
from pathlib import Path
from typing import Any

from etl_engine import duck
from etl_engine.executor import run_flow
from etl_engine.flow import Flow
from etl_engine.registry import Registry
from etl_store import Settings, Store

from etl_worker.notify import notify_run_finished

log = logging.getLogger("etl.worker")


class EventBuffer:
    """Regroupe les événements du moteur en écritures par paquets (un INSERT par événement saturerait SQLite)."""

    def __init__(self, store: Store, run_id: str, interval: float = 0.2) -> None:
        self.store, self.run_id, self.interval = store, run_id, interval
        self._items: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def push(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._items.append(event)

    def flush(self) -> None:
        with self._lock:
            items, self._items = self._items, []
        if items:
            self.store.add_events(self.run_id, items)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.flush()
            except Exception:  # noqa: BLE001 - un échec d'écriture ne doit pas tuer le run
                log.exception("écriture des événements impossible")

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self.flush()


def execute_run(
    store: Store,
    registry: Registry,
    run: dict[str, Any],
    settings: Settings,
    shutdown: threading.Event,
) -> str:
    """Exécute `run` (déjà réservé), enregistre le résultat et renvoie le statut final.

    - annulation demandée par l'utilisateur : statut `cancelled` ;
    - arrêt du worker (SIGTERM) : le run est coupé proprement et marqué `interrupted`, relançable depuis le cache.
    """
    run_id, options = run["id"], run["options"]
    cancel = threading.Event()
    done = threading.Event()
    interrupted = threading.Event()
    events = EventBuffer(store, run_id)

    def watch() -> None:
        while not done.wait(1.0):
            if shutdown.is_set():
                interrupted.set()
                cancel.set()
            elif store.is_cancel_requested(run_id):
                cancel.set()

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    result_json: dict[str, Any] | None = None
    error: str | None = None
    try:
        flow = Flow.model_validate(run["flow"])
        reuse = options.get("reuse_from")
        running = max(1, store.count_running())  # ce run compris : il partage la machine avec les autres
        memory_mb, threads = duck.machine_budget_mb() // running, max(2, duck.machine_threads() // running)
        result = run_flow(
            flow,
            registry,
            Path(settings.cache_dir) / run_id,
            params=run["params"],
            cache=bool(options.get("cache", True)),
            reuse_from=(Path(settings.cache_dir) / reuse) if reuse else None,
            target=options.get("target"),
            on_event=events.push,
            cancel=cancel,
            timeout=settings.run_timeout,
            lang=options.get("locale"),
            flow_path=options.get("flow_path"),
            run_id=run_id,
            user=run.get("owner"),
            memory_mb=memory_mb,
            threads=threads,
        )
        result_json, error = result.to_json(), result.error
        status = result.status
        if status == "cancelled" and interrupted.is_set():
            status = "interrupted"
    except Exception as exc:  # noqa: BLE001 - tout échec est rapporté sur le run, le worker continue
        log.error("run %s : %s", run_id, traceback.format_exc())
        status, error = "error", f"{type(exc).__name__} : {exc}"
        events.push({"type": "run_finished", "status": "error", "error": error})
    finally:
        done.set()
        events.close()
    store.finish_run(run_id, status, error, result_json)
    notify_run_finished(settings, run, status, error, result_json)
    return status
