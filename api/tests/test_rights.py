"""Droits du mode serveur (ADR 0054) : flux, runs, connexions et planifications, partagés ou non."""

from __future__ import annotations

import pytest
from etl_api.auth.providers import hash_password, write_users
from etl_api.main import create_app
from etl_engine.sdk import ConfigError
from etl_store import ConnectionStore, Settings, access
from etl_store.connections import RUN_USER
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("server_store")  # PostgreSQL si ETL_TEST_STORE_URL

HOST = "carriole.exemple.org"
EMPTY = {"name": "vide", "nodes": [], "links": []}
DB_FLOW = {
    "name": "requete",
    "nodes": [{"id": "q", "type": "DatabaseQuery", "params": {"connection": "ma_base", "sql": "SELECT 1"}}],
    "links": [],
}
CONNECTION = {"kind": "postgres", "host": "db.exemple.org", "dbname": "sig", "user": "lecteur", "password": "x"}


@pytest.fixture
def server(tmp_path, monkeypatch):
    users = tmp_path / "users.json"
    accounts = {
        "alice": ["equipe"],
        "carol": ["equipe"],
        "dan": ["autre"],
        "bob": ["carriole-admins"],
    }
    write_users(users, {n: {"password": hash_password(f"mdp-{n}"), "groups": g} for n, g in accounts.items()})
    monkeypatch.setenv("ETL_MODE", "server")
    monkeypatch.setenv("ETL_AUTH", "file")
    monkeypatch.setenv("ETL_USERS_FILE", str(users))
    monkeypatch.delenv("ETL_ADMINS", raising=False)
    settings = Settings(meta_dir=tmp_path / "meta", cache_dir=tmp_path / "cache", plugins_dir=tmp_path / "plugins")
    app = create_app(settings)
    clients: dict[str, TestClient] = {}
    with TestClient(app, base_url=f"http://{HOST}") as first:
        for name in accounts:
            client = first if not clients else TestClient(app, base_url=f"http://{HOST}")
            assert client.post("/api/auth/login", json={"username": name, "password": f"mdp-{name}"}).status_code == 200
            clients[name] = client
        yield clients


def test_a_flow_is_private_until_shared(server):
    alice, carol, dan, bob = server["alice"], server["carol"], server["dan"], server["bob"]
    flow_id = alice.post("/api/flows", json={"flow": EMPTY}).json()["id"]
    assert [f["access"] for f in alice.get("/api/flows").json()] == ["owner"]
    assert carol.get("/api/flows").json() == [] and carol.get(f"/api/flows/{flow_id}").status_code == 404
    assert {f["id"] for f in bob.get("/api/flows").json()} == {flow_id}  # administrateur

    shares = {"shares": [{"principal": "group:equipe", "role": "read"}]}
    assert carol.put(f"/api/flows/{flow_id}/shares", json=shares).status_code == 404
    assert alice.put(f"/api/flows/{flow_id}/shares", json=shares).json() == shares
    assert carol.get(f"/api/flows/{flow_id}").json()["access"] == "read"
    assert dan.get(f"/api/flows/{flow_id}").status_code == 404
    save = {"flow": {**EMPTY, "name": "changé"}}
    assert carol.put(f"/api/flows/{flow_id}", json=save).status_code == 403
    assert carol.put(f"/api/flows/{flow_id}/shares", json=shares).status_code == 403
    assert carol.delete(f"/api/flows/{flow_id}").status_code == 403

    alice.put(f"/api/flows/{flow_id}/shares", json={"shares": [{"principal": "user:carol", "role": "write"}]})
    assert carol.put(f"/api/flows/{flow_id}", json=save).status_code == 200
    assert carol.delete(f"/api/flows/{flow_id}").status_code == 403  # supprimer reste au propriétaire
    assert alice.delete(f"/api/flows/{flow_id}").status_code == 204


def test_invalid_shares_are_refused(server):
    alice = server["alice"]
    flow_id = alice.post("/api/flows", json={"flow": EMPTY}).json()["id"]
    for bad in ({"principal": "carol", "role": "read"}, {"principal": "user:carol", "role": "admin"}):
        assert alice.put(f"/api/flows/{flow_id}/shares", json={"shares": [bad]}).status_code == 422


def test_runs_follow_the_rights_on_their_flow(server):
    alice, carol, dan = server["alice"], server["carol"], server["dan"]
    flow_id = alice.post("/api/flows", json={"flow": EMPTY}).json()["id"]
    run_id = alice.post("/api/runs", json={"flow_id": flow_id}).json()["id"]
    adhoc = dan.post("/api/runs", json={"flow": EMPTY}).json()["id"]  # un brouillon : toujours permis
    assert carol.post("/api/runs", json={"flow_id": flow_id}).status_code == 404
    assert carol.get(f"/api/runs/{run_id}").status_code == 404
    assert run_id not in {r["id"] for r in carol.get("/api/runs").json()}
    assert carol.get(f"/api/data/{run_id}/n/output/info").status_code == 404
    assert carol.get(f"/api/runs/{adhoc}").status_code == 404

    alice.put(f"/api/flows/{flow_id}/shares", json={"shares": [{"principal": "*", "role": "read"}]})
    assert run_id in {r["id"] for r in carol.get("/api/runs").json()}
    assert carol.get(f"/api/runs/{run_id}").status_code == 200
    assert carol.post(f"/api/runs/{run_id}/cancel").status_code == 403  # lire ne suffit pas pour arrêter
    assert carol.post("/api/runs", json={"flow_id": flow_id}).status_code == 201
    assert alice.post(f"/api/runs/{run_id}/cancel").status_code == 200


def test_connections_are_private_until_shared_and_checked_at_run(server):
    alice, carol, bob = server["alice"], server["carol"], server["bob"]
    assert alice.put("/api/connections/ma_base", json=CONNECTION).status_code == 200
    assert carol.get("/api/connections").json() == []
    assert carol.get("/api/connections/ma_base/tables").status_code == 404
    assert carol.put("/api/connections/ma_base", json=CONNECTION).status_code == 404  # pas de vol de nom
    assert carol.post("/api/runs", json={"flow": DB_FLOW}).status_code == 404
    assert carol.post("/api/sql/check", json={"sql": "SELECT 1", "connection": "ma_base"}).status_code == 404

    alice.put("/api/connections/ma_base/shares", json={"shares": [{"principal": "group:equipe", "role": "read"}]})
    listed = carol.get("/api/connections").json()
    assert [(c["name"], c["access"]) for c in listed] == [("ma_base", "read")] and "password" not in listed[0]
    assert carol.put("/api/connections/ma_base", json=CONNECTION).status_code == 403
    assert carol.delete("/api/connections/ma_base").status_code == 403
    assert bob.get("/api/connections").json()[0]["access"] == "owner"

    # le worker revérifie au moment d'ouvrir la connexion (planification, flux modifié entre-temps)
    app = carol.app
    provider = ConnectionStore(app.state.store, app.state.settings.secret_key).provider()
    try:
        RUN_USER.who = access.who(app.state.store, "dan")
        with pytest.raises(ConfigError, match="non partagée"):
            provider("ma_base")
        RUN_USER.who = access.who(app.state.store, "carol")
        assert provider("ma_base").name == "ma_base"
    finally:
        RUN_USER.who = None


def test_schedules_are_their_owners_and_need_a_readable_flow(server):
    alice, carol = server["alice"], server["carol"]
    flow_id = alice.post("/api/flows", json={"flow": EMPTY}).json()["id"]
    body = {"name": "chaque nuit", "flow_id": flow_id, "cron": "0 2 * * *"}
    assert carol.post("/api/schedules", json=body).status_code == 404
    sched = alice.post("/api/schedules", json=body).json()
    assert carol.get("/api/schedules").json() == []
    assert carol.delete(f"/api/schedules/{sched['id']}").status_code == 404
    assert carol.post(f"/api/schedules/{sched['id']}/run").status_code == 404


def test_scheduler_skips_a_flow_no_longer_shared(server, monkeypatch):
    import time

    from etl_worker import scheduler

    alice, carol = server["alice"], server["carol"]
    store = alice.app.state.store
    flow_id = alice.post("/api/flows", json={"flow": EMPTY}).json()["id"]
    alice.put(f"/api/flows/{flow_id}/shares", json={"shares": [{"principal": "user:carol", "role": "read"}]})
    sched = carol.post("/api/schedules", json={"name": "s", "flow_id": flow_id, "cron": "* * * * *"}).json()
    alice.put(f"/api/flows/{flow_id}/shares", json={"shares": []})
    with store.session() as s, s.begin():
        from etl_store.models import ScheduleRow

        s.get(ScheduleRow, sched["id"]).next_run_at = time.time() - 1
    assert scheduler.tick(store) == []
    assert "plus partagé" in store.get_schedule(sched["id"])["last_error"]


def test_known_users_are_offered_for_sharing(server):
    listed = server["carol"].get("/api/users").json()
    assert {u["name"] for u in listed["users"]} == {"alice", "bob", "carol", "dan"}
    assert listed["groups"] == ["autre", "carriole-admins", "equipe"]
