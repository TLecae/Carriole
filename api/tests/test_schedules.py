import sys
import time

import pytest
from etl_api.main import create_app
from etl_engine import components as engine_components
from etl_engine import connections as engine_connections
from etl_store import Settings
from fastapi.testclient import TestClient

FLOW = {
    "name": "Flux planifié",
    "published_params": [{"name": "DEP", "default": "31", "required": True}],
    "nodes": [{"id": "r", "type": "Reader", "params": {"path": "/data/in/x.csv"}}],
    "links": [],
}


@pytest.fixture
def client(tmp_path):
    engine_components.clear_providers()
    engine_connections.clear_providers()
    settings = Settings(meta_dir=tmp_path / "meta", cache_dir=tmp_path / "cache", plugins_dir=tmp_path / "plugins")
    with TestClient(create_app(settings)) as c:
        yield c
    engine_components.clear_providers()
    engine_connections.clear_providers()


def payload(flow_id, **extra):
    return {"name": "Chaque nuit", "flow_id": flow_id, "cron": "30 2 * * *", "params": {"DEP": "09"}, **extra}


def test_create_list_update_delete(client):
    flow = client.post("/api/flows", json={"flow": FLOW}).json()
    created = client.post("/api/schedules", json=payload(flow["id"], notify_email="a@b.fr", cache=True))
    assert created.status_code == 201, created.text
    schedule = created.json()
    assert schedule["flow_name"] == "Flux planifié" and schedule["next_run_at"] > time.time()
    assert schedule["options"] == {"cache": True, "notify_email": "a@b.fr", "allow_overlap": False}
    assert [s["id"] for s in client.get("/api/schedules").json()] == [schedule["id"]]

    updated = client.put(f"/api/schedules/{schedule['id']}", json=payload(flow["id"], cron="0 6 * * 1", enabled=False))
    assert updated.status_code == 200 and updated.json()["enabled"] is False and updated.json()["next_run_at"] is None
    reenabled = client.put(f"/api/schedules/{schedule['id']}", json=payload(flow["id"], cron="0 6 * * 1")).json()
    assert reenabled["next_run_at"] > time.time()

    assert client.delete(f"/api/schedules/{schedule['id']}").status_code == 204
    assert client.delete(f"/api/schedules/{schedule['id']}").status_code == 404
    assert client.put(f"/api/schedules/{schedule['id']}", json=payload(flow["id"])).status_code == 404


def test_validation_errors_are_explicit(client):
    flow = client.post("/api/flows", json={"flow": FLOW}).json()
    bad_cron = client.post("/api/schedules", json=payload(flow["id"], cron="tous les jours"))
    assert bad_cron.status_code == 422 and "cinq champs" in bad_cron.text
    assert client.post("/api/schedules", json=payload("0" * 32)).status_code == 422
    assert client.post("/api/schedules", json=payload(flow["id"], params={"NOPE": "1"})).status_code == 422
    assert client.post("/api/schedules", json=payload(flow["id"], notify_email="pas une adresse")).status_code == 422
    assert client.post("/api/schedules", json=payload(flow["id"], params={})).status_code == 201  # DEP a un défaut


def test_preview_lists_upcoming_occurrences(client):
    ok = client.post("/api/schedules/preview", json={"cron": "0 12 * * *", "count": 3}).json()["next"]
    assert len(ok) == 3 and ok[0] > time.time() and ok[1] - ok[0] in (86400, 82800, 90000)
    assert client.post("/api/schedules/preview", json={"cron": "oups"}).status_code == 422


def test_run_now_creates_a_queued_run_with_the_schedule_settings(client):
    flow = client.post("/api/flows", json={"flow": FLOW}).json()
    schedule = client.post("/api/schedules", json=payload(flow["id"], cache=True)).json()
    run = client.post(f"/api/schedules/{schedule['id']}/run")
    assert run.status_code == 201
    body = run.json()
    assert (
        body["status"] == "queued"
        and body["params"] == {"DEP": "09"}
        and body["options"]["schedule_id"] == schedule["id"]
    )
    assert client.get(f"/api/schedules/{'0' * 32}/run").status_code in (404, 405)
    assert client.post(f"/api/schedules/{'0' * 32}/run").status_code == 404


@pytest.mark.skipif(sys.platform != "win32", reason="lecteurs Windows")
def test_a_schedule_on_a_network_drive_gets_a_warning(client, monkeypatch):
    from etl_engine import drives

    monkeypatch.setattr(drives, "_is_network_drive", lambda letter: letter.upper() == "Z")
    flow = {**FLOW, "nodes": [{"id": "r", "type": "Reader", "params": {"path": "Z:/sig/communes.gpkg"}}]}
    saved = client.post("/api/flows", json={"flow": flow}).json()
    schedule = client.post("/api/schedules", json=payload(saved["id"])).json()
    assert len(schedule["warnings"]) == 1 and "Z:/sig/communes.gpkg" in schedule["warnings"][0]
    local = client.post("/api/flows", json={"flow": FLOW}).json()  # /data/in/x.csv : pas de lettre de lecteur
    assert client.post("/api/schedules", json=payload(local["id"])).json()["warnings"] == []
