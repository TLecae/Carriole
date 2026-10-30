"""Le backend de bureau pour de vrai : processus `etl serve`, jeton, workers, arrêt propre."""

import json
import socket
import subprocess
import sys
import threading
import time

import pytest
from etl_api.serve import GRACEFUL_SECONDS, InstanceLock, WorkerSupervisor, http_server
from etl_engine import backend
from serve_support import make_backend  # noqa: F401 - fixture


def test_serve_runs_a_flow_end_to_end_and_stops_cleanly(make_backend, tmp_path, monkeypatch):
    server = make_backend(workers=1)
    info = server.info
    monkeypatch.setenv("ETL_HOME", str(server.home))

    # le jeton protège l'API ; la santé reste publique
    assert backend.is_healthy(info)
    with pytest.raises(backend.BackendError) as refused:
        backend.call(backend.ServerInfo(info.pid, info.port, "faux", info.started), "GET", "/api/flows")
    assert refused.value.status == 401
    assert backend.running_server() is not None  # server.json écrit

    # un second « etl serve » ne démarre pas : il renvoie le backend déjà en marche
    again = subprocess.run(
        [sys.executable, "-m", "etl_engine.cli", "serve", "--json"],
        capture_output=True,
        text=True,
        env=server.env,
        timeout=60,
    )
    assert again.returncode == 0 and json.loads(again.stdout)["already_running"] is True

    # un flux en fichier, un run exécuté par le worker supervisé
    src = tmp_path / "in.csv"
    src.write_text("id,dep\n1,31\n2,09\n3,31\n", encoding="utf-8")
    out = tmp_path / "sortie" / "res.csv"
    flow = {
        "name": "bureau",
        "nodes": [
            {"id": "r", "type": "Reader", "params": {"path": str(src)}},
            {"id": "t", "type": "ExtractByExpression", "params": {"conditions": ["\"dep\" = '31'"]}},
            {"id": "w", "type": "Writer", "params": {"path": str(out)}},
        ],
        "links": [
            {"id": "l1", "from": ["r", "output"], "to": ["t", "input"]},
            {"id": "l2", "from": ["t", "Passed"], "to": ["w", "input"]},
        ],
    }
    saved = server.call("POST", "/api/flows", {"flow": flow, "path": str(tmp_path / "equipe" / "bureau.json")})
    assert (tmp_path / "equipe" / "bureau.json").is_file()
    run = server.call("POST", "/api/runs", {"flow_id": saved["id"]})
    server.wait_status(run["id"], "done", 60)
    assert out.read_text(encoding="utf-8").count("\n") == 3  # en-tête + 2 lignes

    # arrêt par l'API : le processus se termine, server.json disparaît
    assert server.stop() == 0
    assert backend.read_info() is None

    # journal du backend : lancé sans console par l'application, c'est la seule trace d'un arrêt
    log = (server.home / "data" / "logs" / "server.log").read_text(encoding="utf-8")
    assert "backend démarré" in log and "backend arrêté" in log

    # relance par l'application après un arrêt : même adresse, même jeton, la page ouverte continue sans recharger
    again = make_backend(workers=0, args=["--port", str(info.port)], ETL_SERVE_TOKEN=info.token)
    assert (again.info.port, again.info.token) == (info.port, info.token)
    assert [f["name"] for f in again.call("GET", "/api/flows")] == ["bureau"]  # le flux récent est retrouvé
    assert again.stop() == 0


def test_a_connection_asyncio_never_releases_does_not_block_the_stop(monkeypatch):
    """Sous Windows, `shutdown()` du socket d'un client parti brutalement échoue parfois dans `_call_connection_lost` :
    asyncio ne libère jamais la connexion et `Server.wait_closed()` attend sans fin. L'arrêt reste borné."""

    async def app(scope, receive, send):  # application ASGI minimale : seul l'arrêt du serveur compte ici
        while scope["type"] == "lifespan":
            message = await receive()
            await send({"type": message["type"] + ".complete"})
            if message["type"] == "lifespan.shutdown":
                return

    def reset(self, how):
        raise ConnectionResetError(10054, "connexion fermée par l'hôte distant")

    server = http_server(app)
    sock = socket.create_server(("127.0.0.1", 0))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    monkeypatch.setattr(socket.socket, "shutdown", reset)
    socket.create_connection(sock.getsockname()).close()  # client parti ; côté serveur, shutdown() échoue
    time.sleep(0.5)
    server.should_exit = True
    thread.join(GRACEFUL_SECONDS + 10)
    assert not thread.is_alive()


def test_instance_lock_is_exclusive_and_released(tmp_path):
    first, second = InstanceLock(tmp_path / "l.lock"), InstanceLock(tmp_path / "l.lock")
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()


def test_supervisor_restarts_a_crashing_worker_and_stops_a_healthy_one(tmp_path):
    marker = tmp_path / "starts.txt"
    crash = [sys.executable, "-c", f"open(r'{marker}', 'a').write('x'); raise SystemExit(3)"]
    crashing = WorkerSupervisor(1, command=crash)
    crashing.start()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and (not marker.exists() or len(marker.read_text()) < 2):
        time.sleep(0.2)
    crashing.stop(grace=5)
    assert len(marker.read_text()) >= 2  # relancé après le plantage

    # un worker qui lit son entrée comme le vrai : « stop » = sortie propre, sans arrêt forcé
    listener = [sys.executable, "-c", "import sys\nfor l in sys.stdin:\n    if l.strip() == 'stop':\n        break"]
    healthy = WorkerSupervisor(1, command=listener)
    healthy.start()
    time.sleep(1)
    started = time.monotonic()
    healthy.stop(grace=20)
    assert time.monotonic() - started < 10
