import json
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from etl_api.main import create_app
from etl_store import Settings
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    settings = Settings(meta_dir=tmp_path / "meta", cache_dir=tmp_path / "cache", plugins_dir=tmp_path / "plugins")
    with TestClient(create_app(settings)) as c:
        c.tmp = tmp_path
        yield c


def sample_flow(tmp_path, name="demo"):
    src = tmp_path / "in.parquet"
    pq.write_table(pa.table({"id": np.arange(5), "dep": ["31"] * 5}), src)
    return {
        "name": name,
        "published_params": [{"name": "DEP", "default": "31"}],
        "nodes": [
            {"id": "r", "type": "Reader", "params": {"path": str(src)}},
            {"id": "t", "type": "ExtractByExpression", "params": {"conditions": ["\"dep\" = '${DEP}'"]}},
            {"id": "w", "type": "Writer", "params": {"path": str(tmp_path / "o.parquet")}},
        ],
        "links": [
            {"id": "l1", "from": ["r", "output"], "to": ["t", "input"]},
            {"id": "l2", "from": ["t", "Passed"], "to": ["w", "input"]},
        ],
    }


def test_health_and_version(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/version").json()["version"]


def test_modifiers_palette(client):
    data = client.get("/api/modifiers").json()
    names = {t["name"] for t in data["modifiers"]}
    assert {"Reader", "Writer", "ExtractByExpression", "AddAttributes"} <= names
    tester = next(t for t in data["modifiers"] if t["name"] == "ExtractByExpression")
    assert (
        tester["outputs"] == ["Passed", "Failed", "Rejected"] and "conditions" in tester["params_schema"]["properties"]
    )


def test_flow_crud_with_revision_conflict(client):
    flow = sample_flow(client.tmp)
    created = client.post("/api/flows", json={"flow": flow}).json()
    assert created["revision"] == 1 and created["name"] == "demo"
    got = client.get(f"/api/flows/{created['id']}").json()
    assert got["flow"]["nodes"][0]["id"] == "r"
    ok = client.put(f"/api/flows/{created['id']}", json={"flow": {**flow, "name": "v2"}, "revision": 1})
    assert ok.status_code == 200 and ok.json()["revision"] == 2
    stale = client.put(f"/api/flows/{created['id']}", json={"flow": flow, "revision": 1})
    assert stale.status_code == 409
    assert [f["name"] for f in client.get("/api/flows").json()] == ["v2"]
    assert client.delete(f"/api/flows/{created['id']}").status_code == 204
    assert client.get(f"/api/flows/{created['id']}").status_code == 404


def test_analyze_returns_schemas_and_issues(client):
    body = {"flow": sample_flow(client.tmp)}
    rep = client.post("/api/analyze", json=body).json()
    assert [f["name"] for f in rep["schemas"]["r/output"]["fields"]] == ["id", "dep"]
    bad = sample_flow(client.tmp)
    bad["nodes"][1]["params"]["conditions"] = []
    assert any(
        i["level"] == "error" and i["node"] == "t"
        for i in client.post("/api/analyze", json={"flow": bad}).json()["issues"]
    )


def test_create_run_validates_and_queues(client):
    flow = sample_flow(client.tmp)
    run = client.post("/api/runs", json={"flow": flow, "params": {"DEP": "09"}}, headers={"X-ETL-User": "alice"})
    assert run.status_code == 201
    body = run.json()
    assert body["status"] == "queued" and body["owner"] == "alice" and body["params"] == {"DEP": "09"}
    assert client.get(f"/api/runs/{body['id']}").json()["flow"]["name"] == "demo"
    assert [
        r["id"] for r in client.get("/api/runs?status=queued&mine=true", headers={"X-ETL-User": "alice"}).json()
    ] == [body["id"]]
    assert client.get("/api/runs?mine=true", headers={"X-ETL-User": "bob"}).json() == []

    bad = sample_flow(client.tmp)
    bad["nodes"][1]["params"]["conditions"] = []
    r = client.post("/api/runs", json={"flow": bad})
    assert r.status_code == 422 and r.json()["detail"]["issues"][0]["node"] == "t"
    assert client.post("/api/runs", json={"flow": flow, "params": {"INCONNU": "1"}}).status_code == 422
    assert client.post("/api/runs", json={"flow": flow, "target": "zz"}).status_code == 422
    assert client.post("/api/runs", json={}).status_code == 422


def test_cancel_queued_run(client):
    rid = client.post("/api/runs", json={"flow": sample_flow(client.tmp)}).json()["id"]
    assert client.post(f"/api/runs/{rid}/cancel").json()["status"] == "cancelled"
    assert client.post("/api/runs/inconnu/cancel").status_code == 404


def test_events_json_and_sse_resume(client):
    rid = client.post("/api/runs", json={"flow": sample_flow(client.tmp)}).json()["id"]
    store = client.app.state.store
    store.add_events(
        rid, [{"type": "run_started", "nodes": ["r"]}, {"type": "counts", "node": "r", "port": "output", "count": 5}]
    )
    store.add_events(rid, [{"type": "log", "node": "r", "level": "WARNING", "message": "attention"}])
    events = client.get(f"/api/runs/{rid}/events?stream=false").json()
    assert [e["type"] for e in events] == ["run_started", "counts", "log"]
    store.finish_run(rid, "done", None, {"status": "done", "nodes": {}})

    with client.stream("GET", f"/api/runs/{rid}/events", headers={"Last-Event-ID": str(events[0]["id"])}) as resp:
        text = "".join(resp.iter_text())
    ids = [line.split(": ")[1] for line in text.splitlines() if line.startswith("id: ")]
    assert ids == [str(events[1]["id"]), str(events[2]["id"])]  # reprise : pas de rejeu du premier événement
    assert "event: end" in text and '"status": "done"' in text

    assert client.get(f"/api/runs/{rid}").json()["counts"] == {"r/output": 5}
    logs = client.get(f"/api/runs/{rid}/logs?level=WARNING").json()
    assert [e["message"] for e in logs] == ["attention"]
    assert client.get(f"/api/runs/{rid}/logs?level=ERROR").json() == []
    assert client.get(f"/api/runs/{rid}/logs?node=autre").json() == []


def test_sse_sends_the_last_events_written_just_before_the_run_ends(client, monkeypatch):
    """Le worker écrit ses derniers événements puis clôt le run : arrivés entre la lecture des événements (aucun) et
    celle du statut (final), ils partent quand même avant `end` (sinon l'écriture finie reste « en cours »)."""
    rid = client.post("/api/runs", json={"flow": sample_flow(client.tmp)}).json()["id"]
    store = client.app.state.store
    store.add_events(rid, [{"type": "run_started", "nodes": ["w"]}, {"type": "node", "node": "w", "status": "running"}])
    get_run = store.get_run
    calls = []

    def worker_ends_meanwhile(run_id, *args, **kwargs):
        calls.append(run_id)
        if len(calls) == 2:  # 1 : existence du run ; 2 : statut lu après une lecture sans événement
            store.add_events(run_id, [{"type": "node", "node": "w", "status": "done"}, {"type": "run_finished"}])
            store.finish_run(run_id, "done", None, {"status": "done", "nodes": {}})
        return get_run(run_id, *args, **kwargs)

    monkeypatch.setattr(store, "get_run", worker_ends_meanwhile)
    with client.stream("GET", f"/api/runs/{rid}/events") as resp:
        text = "".join(resp.iter_text())
    assert text.count("event: node") == 2 and "event: run_finished" in text
    assert text.index("event: run_finished") < text.index("event: end")


def test_end_to_end_with_a_worker(client):
    """L'API crée le job, un worker l'exécute dans son coin, l'API relaie les événements."""
    import threading

    from etl_worker.main import serve

    flow = sample_flow(client.tmp)
    rid = client.post("/api/runs", json={"flow": flow}).json()["id"]
    shutdown = threading.Event()
    app = client.app
    worker = threading.Thread(
        target=serve, args=(app.state.store, app.state.registry, app.state.settings, shutdown, "w")
    )
    import os

    os.environ["ETL_HEARTBEAT_FILE"] = str(client.tmp / "hb")
    worker.start()
    try:
        for _ in range(100):
            status = client.get(f"/api/runs/{rid}").json()["status"]
            if status in ("done", "error"):
                break
            time.sleep(0.1)
    finally:
        shutdown.set()
        worker.join(timeout=5)
    detail = client.get(f"/api/runs/{rid}").json()
    assert detail["status"] == "done", detail["error"]
    assert detail["result"]["nodes"]["t"]["rows"]["Passed"] == 5 and detail["counts"]["r/output"] == 5
    assert json.loads(json.dumps(detail["result"]))["status"] == "done"
    assert pq.read_table(client.tmp / "o.parquet").num_rows == 5
