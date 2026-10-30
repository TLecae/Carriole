"""Backend local de l'application de bureau : l'API et ses workers dans un seul programme (`etl serve`).

- Un seul backend par utilisateur : un verrou de fichier (relâché par le système si le processus meurt) et un fichier
  `server.json` (adresse, jeton) que l'application et la ligne de commande lisent pour le retrouver.
- Écoute sur 127.0.0.1, port libre choisi au démarrage, jeton propre à chaque démarrage (voir `etl_api.security`).
- Les workers sont des processus fils, relancés s'ils plantent. Ils reçoivent leurs ordres sur leur entrée standard
  (`stop`) : arrêt propre sans dépendre des signaux de console Windows, et arrêt aussi si le superviseur disparaît.
- Journal `logs/server.log` (démarrage, workers relancés, erreurs des requêtes) et `logs/server-crash.log` (pile d'un
  plantage natif : GEOS, DuckDB…) : l'application lance le backend sans console, rien ne serait gardé sinon.
"""

from __future__ import annotations

import contextlib
import faulthandler
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO, TYPE_CHECKING

from etl_engine import settings as user_settings
from etl_engine.backend import ServerInfo, info_path, is_healthy, read_info, running_server  # noqa: F401

if TYPE_CHECKING:
    import uvicorn

log = logging.getLogger("etl.serve")

DEFAULT_WORKERS = 3
STOP_GRACE = 60.0  # un run en cours est coupé proprement (« interrompu ») ; au-delà, on tue le worker
# Requêtes en cours (export, flux d'événements d'une page ouverte…) attendues à l'arrêt ; au-delà, coupées. Sans borne,
# l'arrêt pouvait ne jamais finir sous Windows : quand un client coupe brutalement sa connexion, `shutdown()` du socket
# échoue parfois dans `_call_connection_lost` (« WinError 10054 » dans server.log) et asyncio ne libère jamais la
# connexion : `Server.wait_closed()`, la dernière attente d'uvicorn, ne rend plus la main (Python 3.12 à 3.14).
GRACEFUL_SECONDS = 5.0


def http_server(app) -> uvicorn.Server:
    """Serveur HTTP du backend, à l'arrêt borné (`GRACEFUL_SECONDS`)."""
    import uvicorn

    config = uvicorn.Config(app, log_level="warning", access_log=False, timeout_graceful_shutdown=GRACEFUL_SECONDS)
    return uvicorn.Server(config)


class InstanceLock:
    """Verrou exclusif de fichier : relâché par le système à la mort du processus (pas de verrou périmé)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: IO[bytes] | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")  # noqa: SIM115 - gardé ouvert tant que le verrou est tenu
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is not None:
            with contextlib.suppress(OSError):
                self._handle.close()
            self._handle = None


class WorkerSupervisor:
    """Maintient N workers vivants ; les arrête proprement (`stop` sur leur entrée standard)."""

    def __init__(self, count: int, command: list[str] | None = None, log_dir: Path | None = None) -> None:
        self.count = count
        self.command = command or [sys.executable, "-m", "etl_worker.main"]
        self.log_dir = log_dir
        self.stopping = threading.Event()
        self._procs: dict[int, subprocess.Popen] = {}
        self._threads: list[threading.Thread] = []

    def _spawn(self, index: int) -> subprocess.Popen:
        env = {**os.environ, "ETL_WORKER_STDIN": "1", "ETL_API_TOKEN": ""}  # le worker n'a pas besoin du jeton
        env.pop("ETL_SERVE_TOKEN", None)
        # journal lisible (UTF-8) et pile d'un plantage natif écrite dans le journal du worker
        env.update(PYTHONIOENCODING="utf-8", PYTHONFAULTHANDLER="1")
        output: IO[bytes] | int = subprocess.DEVNULL
        if self.log_dir is not None:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            output = open(self.log_dir / f"worker-{index}.log", "ab")  # noqa: SIM115 - fermé avec le processus
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            return subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=output,
                stderr=subprocess.STDOUT,
                env=env,
                creationflags=flags,
            )
        finally:
            if not isinstance(output, int):
                output.close()  # le fils a sa propre copie du descripteur

    def _keep_alive(self, index: int) -> None:
        delay = 1.0
        while not self.stopping.is_set():
            started = time.monotonic()
            proc = self._procs[index] = self._spawn(index)
            log.info("worker %d démarré (pid %d)", index, proc.pid)
            while proc.poll() is None and not self.stopping.wait(0.5):
                pass
            if self.stopping.is_set():
                return
            lived = time.monotonic() - started
            delay = 1.0 if lived > 60 else min(delay * 2, 30.0)  # un worker qui plante en boucle ne sature pas le poste
            log.warning("worker %d arrêté (code %s), relance dans %.0f s", index, proc.returncode, delay)
            self.stopping.wait(delay)

    def start(self) -> None:
        for index in range(1, self.count + 1):
            thread = threading.Thread(target=self._keep_alive, args=(index,), name=f"worker-{index}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self, grace: float = STOP_GRACE) -> None:
        self.stopping.set()
        for thread in self._threads:
            thread.join(timeout=2)
        procs = list(self._procs.values())
        for proc in procs:
            if proc.poll() is None and proc.stdin:
                with contextlib.suppress(OSError):
                    proc.stdin.write(b"stop\n")
                    proc.stdin.flush()
        deadline = time.monotonic() + grace
        for proc in procs:
            try:
                proc.wait(timeout=max(deadline - time.monotonic(), 0.1))
            except subprocess.TimeoutExpired:
                log.warning("worker %d ne s'arrête pas : arrêt forcé", proc.pid)
                proc.kill()
            if proc.stdin:
                with contextlib.suppress(OSError):
                    proc.stdin.close()


def _log_to_files(folder: Path) -> IO[str]:
    """Journal du backend et pile des plantages natifs dans `folder` ; renvoie le fichier de `faulthandler` (à garder
    ouvert)."""
    folder.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(folder / "server.log", maxBytes=5_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    # racine : etl.* ; « uvicorn.error » ne remonte pas jusqu'à elle (erreurs des requêtes, exceptions non gérées)
    for name in ("", "uvicorn.error"):
        logging.getLogger(name).addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    crash = open(folder / "server-crash.log", "a", encoding="utf-8")  # noqa: SIM115 - ouvert tant que le backend vit
    faulthandler.enable(crash)
    return crash


def _listening_socket(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform != "win32":
        # POSIX : reprendre la même adresse aussitôt après un arrêt (connexions en TIME_WAIT). Pas sous Windows, où
        # SO_REUSEADDR laisserait deux processus écouter le même port.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    return sock


def run_server(port: int = 0, workers: int | None = None, announce: bool = False) -> int:
    """Démarre le backend et ne rend la main qu'à l'arrêt. Code de sortie : 0 arrêt normal (ou déjà démarré)."""
    from etl_store import Settings

    from etl_api.main import create_app

    dirs = user_settings.app_dirs()
    lock = InstanceLock(dirs.data / "server.lock")
    if not lock.acquire():
        existing = running_server()
        if announce and existing:
            print(json.dumps({**existing.as_json(), "already_running": True}), flush=True)
        else:
            print(f"Le backend est déjà démarré : {existing.url if existing else 'adresse inconnue'}", file=sys.stderr)
        return 0 if existing else 1

    # Poste de bureau : les flux sont des fichiers (workers compris). `ETL_FLOW_STORAGE=db` reste possible (essais).
    os.environ.setdefault("ETL_FLOW_STORAGE", "files")
    # Poste de bureau : le cache d'un run n'est gardé que tant que son flux est ouvert (etl_api.cache_hold).
    os.environ.setdefault("ETL_CACHE_POLICY", "session")
    user_settings.apply_environment()  # proxy et budget mémoire, hérités par les workers
    token = os.environ.get("ETL_SERVE_TOKEN") or secrets.token_urlsafe(32)  # jeton imposé : tests de bout en bout
    sock = _listening_socket(port)
    real_port = sock.getsockname()[1]
    info = ServerInfo(os.getpid(), real_port, token, time.time())
    settings = Settings(api_token=token, allowed_hosts=(f"127.0.0.1:{real_port}", f"localhost:{real_port}"))
    count = workers if workers is not None else int(user_settings.get("workers", DEFAULT_WORKERS))
    # Poste de bureau : un seul utilisateur, donc autant de flux simultanés que de workers (« Runs en parallèle »).
    # Les limites ETL_MAX_RUNNING* restent réglables par l'environnement (mode serveur).
    os.environ.setdefault("ETL_MAX_RUNNING", str(count))
    os.environ.setdefault("ETL_MAX_RUNNING_PER_USER", "0")

    app = create_app(settings)
    server = http_server(app)
    crash_log = _log_to_files(dirs.logs)  # après `http_server` (uvicorn.Config configure ses propres journaux)
    log.info("backend démarré (pid %d, port %d, %d workers)", os.getpid(), real_port, count)

    app.state.request_shutdown = lambda: setattr(server, "should_exit", True)

    def publish_when_ready() -> None:
        """`server.json` (et l'annonce sur la sortie standard) n'apparaissent qu'une fois l'API prête à répondre."""
        while not server.started and not server.should_exit:
            time.sleep(0.05)
        if server.started:
            info_path().write_text(json.dumps(asdict(info)), encoding="utf-8")
            if announce:
                print(json.dumps(info.as_json()), flush=True)

    supervisor = WorkerSupervisor(count, log_dir=dirs.logs)
    try:
        supervisor.start()
        threading.Thread(target=publish_when_ready, name="etl-announce", daemon=True).start()
        server.run(sockets=[sock])
    finally:
        log.info("arrêt des workers")
        supervisor.stop()
        with contextlib.suppress(OSError):
            info_path().unlink()
        sock.close()
        lock.release()
        log.info("backend arrêté")
        faulthandler.disable()
        crash_log.close()
    return 0
