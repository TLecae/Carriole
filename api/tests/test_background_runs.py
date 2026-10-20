"""Runs en arrière-plan sur un vrai backend de bureau :

1. arrêt propre du backend en plein run  -> run « interrompu », puis reprise depuis le cache
2. worker tué en plein run               -> le run est détecté orphelin, puis reprise depuis le cache
3. annulation d'un run en cours
"""

import time
from pathlib import Path

import psutil
import pytest
from serve_support import make_backend  # noqa: F401 - fixture

pytestmark = pytest.mark.slow
PLUGINS = Path(__file__).resolve().parents[2] / "e2e" / "plugins"


def slow_flow(csv: Path) -> dict:
    """SlowPass (plugin de test) retient le lot 25 s : le run dure assez pour être coupé."""
    return {
        "name": "flux lent",
        "nodes": [
            {"id": "Reader_1", "type": "Reader", "params": {"path": str(csv), "delimiter": ";"}},
            {"id": "Slow_1", "type": "SlowPass", "params": {"seconds": 25}},
        ],
        "links": [{"id": "l1", "from": ["Reader_1", "output"], "to": ["Slow_1", "input"]}],
    }


@pytest.fixture
def csv(tmp_path):
    path = tmp_path / "communes.csv"
    path.write_text("id;nom\n" + "".join(f"{i};commune {i}\n" for i in range(20)), encoding="utf-8")
    return path


def start_slow_run(server, csv: Path) -> str:
    run = server.call("POST", "/api/runs", {"flow": slow_flow(csv), "cache": True})
    server.wait_status(run["id"], "running", 60)
    time.sleep(3)  # laisse le lecteur finir : son résultat sera dans le cache
    return run["id"]


def test_clean_shutdown_interrupts_the_run_then_resumes_from_cache(make_backend, csv):
    server = make_backend(workers=1, ETL_PLUGINS_DIR=str(PLUGINS))
    run_id = start_slow_run(server, csv)

    assert server.stop() == 0  # arrêt propre en plein run : le worker marque le run « interrompu »

    restarted = make_backend(workers=1, home=server.home, ETL_PLUGINS_DIR=str(PLUGINS))
    interrupted = restarted.call("GET", f"/api/runs/{run_id}")
    assert interrupted["status"] == "interrupted"
    assert interrupted["result"]["nodes"]["Reader_1"]["status"] == "done"

    resumed = restarted.call("POST", f"/api/runs/{run_id}/retry", {"resume": True})
    done = restarted.wait_status(resumed["id"], "done", 180)
    assert done["result"]["nodes"]["Reader_1"]["status"] == "cached"  # le lecteur n'est pas relu


def test_killed_worker_leaves_an_orphan_that_is_detected_and_resumed(make_backend, csv):
    server = make_backend(workers=1, ETL_PLUGINS_DIR=str(PLUGINS), ETL_STALE_SECONDS="12")
    run_id = start_slow_run(server, csv)

    workers = [p for p in psutil.Process(server.info.pid).children() if "etl_worker" in " ".join(p.cmdline())]
    assert workers, "aucun worker trouvé parmi les processus fils"
    for worker in workers:
        worker.kill()  # arrêt brutal : aucun nettoyage possible
    server.wait_status(run_id, "interrupted", 90)  # heartbeat expiré, détecté sans attendre le nouveau worker

    resumed = server.call("POST", f"/api/runs/{run_id}/retry", {"resume": True})
    server.wait_status(resumed["id"], "done", 180)  # le superviseur a relancé un worker


def test_a_running_run_can_be_cancelled(make_backend, csv):
    server = make_backend(workers=1, ETL_PLUGINS_DIR=str(PLUGINS))
    run = server.call("POST", "/api/runs", {"flow": slow_flow(csv), "cache": True})
    server.wait_status(run["id"], "running", 60)
    server.call("POST", f"/api/runs/{run['id']}/cancel")
    server.wait_status(run["id"], "cancelled", 90)
