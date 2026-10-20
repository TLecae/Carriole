"""Boucle du worker : prend un run à la fois, tient son heartbeat, s'arrête proprement sur SIGTERM/SIGINT/SIGBREAK."""

from __future__ import annotations

import logging
import os
import signal
import socket
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from etl_engine import components as engine_components
from etl_engine import connections as engine_connections
from etl_engine.registry import Registry
from etl_store import ConnectionStore, Settings, Store, open_store
from etl_store.purge import purge_cache

from etl_worker import scheduler
from etl_worker.runner import execute_run

log = logging.getLogger("etl.worker")

HEARTBEAT_INTERVAL = 5.0
POLL_INTERVAL = 1.0
SWEEP_INTERVAL = 15.0


def heartbeat_path() -> Path | None:
    """Fichier de heartbeat facultatif (sonde externe) ; le heartbeat des runs est de toute façon en base."""
    explicit = os.environ.get("ETL_HEARTBEAT_FILE")
    return Path(explicit) if explicit else None


def watch_stdin(shutdown: threading.Event) -> None:
    """Superviseur (`etl serve`) : « stop » ou fermeture de l'entrée = arrêt propre. Le worker ne survit pas
    à un superviseur disparu."""
    try:
        for line in sys.stdin:
            if line.strip() == "stop":
                break
    except (OSError, ValueError):
        pass
    log.info("arrêt demandé par le superviseur")
    shutdown.set()


def run(
    stop: threading.Event,
    heartbeat: Path | None,
    interval: float = HEARTBEAT_INTERVAL,
    on_beat: Callable[[], None] | None = None,
) -> None:
    """Écrit le fichier de heartbeat (s'il y en a un) et appelle `on_beat` à chaque battement."""
    while not stop.is_set():
        if heartbeat is not None:
            heartbeat.write_text(str(time.time()))
        if on_beat:
            on_beat()
        stop.wait(interval)


def serve(store: Store, registry: Registry, settings: Settings, shutdown: threading.Event, worker_id: str) -> None:
    current: dict[str, str | None] = {"run": None}

    def beat() -> None:
        if current["run"]:
            store.heartbeat(current["run"])

    heart = threading.Thread(target=run, args=(shutdown, heartbeat_path(), HEARTBEAT_INTERVAL, beat), daemon=True)
    heart.start()
    # Le planificateur a son propre fil : il continue de tourner pendant qu'un run s'exécute dans ce worker.
    threading.Thread(target=scheduler.loop, args=(store, shutdown), name="etl-scheduler", daemon=True).start()
    last_sweep = 0.0
    while not shutdown.is_set():
        if time.monotonic() - last_sweep > SWEEP_INTERVAL:
            for rid in store.interrupt_stale_runs(settings.stale_seconds):
                log.warning("run %s sans heartbeat : marqué interrompu", rid)
            last_sweep = time.monotonic()
        if store.try_acquire("purge", settings.purge_interval):  # un seul worker par période
            report = purge_cache(store, settings.cache_dir, settings.cache_retention_days, settings.cache_max_bytes)
            if report.deleted or report.orphans:
                log.info(
                    "cache purgé : %d run(s), %d Mo restants", len(report.deleted), report.remaining_bytes // 2**20
                )
        claimed = store.claim_next_run(worker_id, settings.max_running, settings.max_running_per_user)
        if claimed is None:
            shutdown.wait(POLL_INTERVAL)
            continue
        current["run"] = claimed["id"]
        log.info("run %s (%s) démarré", claimed["id"], claimed["flow_name"])
        try:
            status = execute_run(store, registry, claimed, settings, shutdown)
            log.info("run %s terminé : %s", claimed["id"], status)
        finally:
            current["run"] = None


def main() -> None:
    logging.basicConfig(level=os.environ.get("ETL_LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(message)s")
    settings = Settings()
    store = open_store(settings)
    engine_connections.install_provider(ConnectionStore(store, settings.secret_key).provider(), key="store")
    if provider := store.components_provider():
        engine_components.install_provider(provider, key="store")
    registry = Registry.discover(settings.plugins_dir)
    shutdown = threading.Event()

    def _stop(signum: int, _frame: object) -> None:
        log.info("signal %s reçu, arrêt propre", signal.Signals(signum).name)
        shutdown.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGBREAK"):  # Windows : le superviseur (`etl serve`) envoie CTRL_BREAK_EVENT
        signal.signal(signal.SIGBREAK, _stop)
    if os.environ.get("ETL_WORKER_STDIN"):
        threading.Thread(target=watch_stdin, args=(shutdown,), name="etl-stdin", daemon=True).start()
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    log.info("worker %s démarré", worker_id)
    serve(store, registry, settings, shutdown, worker_id)
    log.info("worker %s arrêté", worker_id)


if __name__ == "__main__":
    main()
