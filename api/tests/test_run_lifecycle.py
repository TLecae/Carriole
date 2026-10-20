import time

import pytest
from etl_api.main import create_app
from etl_store import Settings
from fastapi.testclient import TestClient

FLOW = {
    "name": "cycle de vie",
    "nodes": [{"id": "r", "type": "Reader", "params": {"path": "/x.csv"}}],
    "links": [],
}


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        meta_dir=tmp_path / "meta", cache_dir=tmp_path / "cache", plugins_dir=tmp_path / "p", stale_seconds=0.6
    )
    with TestClient(create_app(settings)) as c:
        c.tmp = tmp_path
        yield c


def test_notify_email_is_validated_and_kept_in_options(client):
    bad = client.post("/api/runs", json={"flow": FLOW, "notify_email": "pas une adresse"})
    assert bad.status_code == 422
    ok = client.post("/api/runs", json={"flow": FLOW, "notify_email": "  moi@exemple.org "})
    assert ok.status_code == 201 and ok.json()["options"]["notify_email"] == "moi@exemple.org"
    none = client.post("/api/runs", json={"flow": FLOW}).json()
    assert none["options"]["notify_email"] is None


def finished_run(client, status="interrupted"):
    rid = client.post("/api/runs", json={"flow": FLOW, "params": {}}, headers={"X-ETL-User": "alice"}).json()["id"]
    store = client.app.state.store
    store.claim_next_run("w")
    store.finish_run(rid, status, "coupé", {"status": status, "nodes": {}})
    return rid


def test_retry_uses_the_frozen_flow_and_can_resume_from_the_cache(client):
    rid = finished_run(client)
    # le flux enregistré change ensuite : la reprise rejoue le flux tel qu'il a été exécuté
    again = client.post(f"/api/runs/{rid}/retry", json={"resume": True}, headers={"X-ETL-User": "bob"})
    assert again.status_code == 201
    body = again.json()
    assert body["status"] == "queued" and body["owner"] == "bob" and body["options"]["reuse_from"] == rid
    assert client.get(f"/api/runs/{body['id']}").json()["flow"]["name"] == "cycle de vie"
    fresh = client.post(f"/api/runs/{rid}/retry", json={}).json()
    assert fresh["options"]["reuse_from"] is None


def test_retry_refuses_unfinished_or_unknown_runs(client):
    rid = client.post("/api/runs", json={"flow": FLOW}).json()["id"]
    assert client.post(f"/api/runs/{rid}/retry", json={}).status_code == 409
    assert client.post("/api/runs/inconnu/retry", json={}).status_code == 404


def test_api_marks_orphaned_runs_interrupted_without_any_worker(client):
    rid = client.post("/api/runs", json={"flow": FLOW}).json()["id"]
    client.app.state.store.claim_next_run("worker-mort")  # le worker « disparaît » : plus aucun heartbeat
    for _ in range(40):
        run = client.get(f"/api/runs/{rid}").json()
        if run["status"] == "interrupted":
            break
        time.sleep(0.15)
    assert run["status"] == "interrupted" and "worker" in run["error"]


def test_disk_usage(client):
    cache = client.app.state.settings.cache_dir
    (cache / "abc" / "n").mkdir(parents=True)
    (cache / "abc" / "n" / "f").write_bytes(b"x" * 1000)
    finished_run(client, "done")
    d = client.get("/api/disk").json()
    assert d["cache_bytes"] >= 1000 and d["retention_days"] == 14 and d["runs_with_data"] == 1
    assert d["max_bytes"] == 50 * 2**30
