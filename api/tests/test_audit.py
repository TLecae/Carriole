"""Journal d'activité du mode serveur (ADR 0056)."""

from __future__ import annotations

import pytest
from etl_api.auth.accounts import hash_password, write_users
from etl_api.main import create_app
from etl_store import Settings
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("server_store")  # PostgreSQL si ETL_TEST_STORE_URL

HOST = "carriole.exemple.org"
EMPTY = {"name": "vide", "nodes": [], "links": []}


@pytest.fixture
def app(tmp_path, monkeypatch):
    users = tmp_path / "users.json"
    write_users(
        users,
        {
            "alice": {"password": hash_password("mdp-alice"), "groups": ["equipe"]},
            "bob": {"password": hash_password("mdp-bob"), "groups": ["carriole-admins"]},
        },
    )
    monkeypatch.setenv("ETL_MODE", "server")
    monkeypatch.setenv("ETL_USERS_FILE", str(users))
    monkeypatch.delenv("ETL_ADMINS", raising=False)
    return create_app(Settings(meta_dir=tmp_path / "m", cache_dir=tmp_path / "c", plugins_dir=tmp_path / "p"))


def test_writes_and_sign_ins_are_logged_without_secrets(app):
    with TestClient(app, base_url=f"http://{HOST}") as alice:
        assert alice.post("/api/auth/login", json={"username": "alice", "password": "faux"}).status_code == 401
        alice.post("/api/auth/login", json={"username": "alice", "password": "mdp-alice"})
        flow_id = alice.post("/api/flows", json={"flow": EMPTY}).json()["id"]
        alice.put(f"/api/flows/{flow_id}", json={"flow": {**EMPTY, "name": "renommé"}})
        shares = [{"principal": "group:equipe", "role": "read"}]
        alice.put(f"/api/flows/{flow_id}/shares", json={"shares": shares})
        alice.get(f"/api/flows/{flow_id}")  # une lecture n'est pas consignée
        assert alice.get("/api/audit").status_code == 403
        alice.post("/api/auth/logout")
    with TestClient(app, base_url=f"http://{HOST}") as bob:
        bob.post("/api/auth/login", json={"username": "bob", "password": "mdp-bob"})
        bob.patch("/api/settings", json={"workers": 2})
        log = bob.get("/api/audit").json()
    actions = [(e["user"], e["action"], e["target"]) for e in reversed(log)]
    assert actions == [
        ("alice", "login.failed", ""),
        ("alice", "login", ""),
        ("alice", "flow.create", flow_id),
        ("alice", "flow.update", flow_id),
        ("alice", "flow.share", flow_id),
        ("alice", "logout", ""),
        ("bob", "login", ""),
        ("bob", "settings.update", ""),
    ]
    by_action = {e["action"]: e for e in log}
    assert by_action["flow.update"]["detail"] == {"name": "renommé"}
    assert by_action["flow.share"]["detail"] == {"shares": shares}
    assert by_action["settings.update"]["detail"] == {"keys": ["workers"]}
    assert by_action["login.failed"]["detail"] == {"status": 401}
    assert all("faux" not in str(e) and "mdp-" not in str(e) for e in log)
    assert all(e["ip"] for e in log)


def test_log_pages_and_filters(app):
    with TestClient(app, base_url=f"http://{HOST}") as bob:
        bob.post("/api/auth/login", json={"username": "bob", "password": "mdp-bob"})
        for _ in range(3):
            bob.post("/api/flows", json={"flow": EMPTY})
        first = bob.get("/api/audit", params={"action": "flow", "limit": 2}).json()
        rest = bob.get("/api/audit", params={"action": "flow", "before": first[-1]["id"]}).json()
        assert len(first) == 2 and len(rest) == 1 and {e["action"] for e in first + rest} == {"flow.create"}
        assert bob.get("/api/audit", params={"user": "personne"}).json() == []


def test_desktop_keeps_no_log(tmp_path, monkeypatch):
    monkeypatch.delenv("ETL_MODE", raising=False)
    app = create_app(Settings(meta_dir=tmp_path / "m", cache_dir=tmp_path / "c", plugins_dir=tmp_path / "p"))
    with TestClient(app) as client:
        client.post("/api/flows", json={"flow": EMPTY})
        assert client.get("/api/audit").json() == []
        assert client.app.state.store.list_audit() == []
