"""Mode serveur (ADR 0052) : identité exigée, sessions, proxy de confiance, administrateurs."""

from __future__ import annotations

import pytest
from etl_api.auth.accounts import hash_password, write_users
from etl_api.main import create_app
from etl_store import Settings
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("server_store")  # PostgreSQL si ETL_TEST_STORE_URL

HOST = "carriole.exemple.org"


@pytest.fixture
def server_env(tmp_path, monkeypatch):
    users = tmp_path / "users.json"
    write_users(
        users,
        {
            "alice": {"password": hash_password("motdepasse-a"), "display": "Alice", "groups": ["equipe"]},
            "bob": {"password": hash_password("motdepasse-b"), "groups": ["carriole-admins"]},
        },
    )
    monkeypatch.setenv("ETL_MODE", "server")
    monkeypatch.setenv("ETL_USERS_FILE", str(users))
    monkeypatch.delenv("ETL_ADMINS", raising=False)
    return tmp_path


def make_client(tmp_path, **kwargs) -> TestClient:
    settings = Settings(meta_dir=tmp_path / "meta", cache_dir=tmp_path / "cache", plugins_dir=tmp_path / "plugins")
    return TestClient(create_app(settings), base_url=f"http://{HOST}", **kwargs)


def login(client: TestClient, name: str, password: str):
    return client.post("/api/auth/login", json={"username": name, "password": password})


def test_server_mode_requires_a_session(server_env):
    with make_client(server_env) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/auth/info").json() == {"mode": "server", "password": True}
        assert client.get("/api/flows").status_code == 401
        assert client.get("/tiles/r/n/p/0/0/0.pbf").status_code == 401
        # un en-tête d'identité posé par le navigateur ne vaut rien en mode serveur
        assert client.get("/api/flows", headers={"X-ETL-User": "bob"}).status_code == 401


def test_login_opens_a_session_and_logout_closes_it(server_env):
    with make_client(server_env) as client:
        assert login(client, "alice", "mauvais").status_code == 401
        response = login(client, "alice", "motdepasse-a")
        assert response.status_code == 200
        assert response.json() == {"name": "alice", "display": "Alice", "groups": ["equipe"], "admin": False}
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        me = client.get("/api/auth/me").json()
        assert me["name"] == "alice" and me["mode"] == "server" and me["logout"] is True
        # le propriétaire d'un flux est l'utilisateur connecté, quel que soit l'en-tête envoyé
        flow = {"name": "essai", "nodes": [], "links": []}
        created = client.post("/api/flows", json={"flow": flow}, headers={"X-ETL-User": "bob"}).json()
        assert created["owner"] == "alice"
        assert client.post("/api/auth/logout").status_code == 204
        assert client.get("/api/flows").status_code == 401


def test_session_survives_a_restart_and_is_stored_hashed(server_env):
    with make_client(server_env) as client:
        token = login(client, "alice", "motdepasse-a").cookies["etl_session"]
    with make_client(server_env, cookies={"etl_session": token}) as client:
        assert client.get("/api/auth/me").json()["name"] == "alice"
        from sqlalchemy import text

        with client.app.state.store.session() as session:
            rows = session.execute(text("SELECT id FROM sessions")).all()
        assert rows and all(token not in r[0] for r in rows)


def test_administrators_come_from_their_group(server_env):
    with make_client(server_env) as client:
        assert login(client, "bob", "motdepasse-b").json()["admin"] is True
    with make_client(server_env) as client:
        login(client, "alice", "motdepasse-a")
        assert client.post("/api/shutdown").status_code == 403


def test_admins_setting_names_users_and_groups(server_env, monkeypatch):
    monkeypatch.setenv("ETL_ADMINS", "alice")
    with make_client(server_env) as client:
        assert login(client, "alice", "motdepasse-a").json()["admin"] is True
        assert login(client, "bob", "motdepasse-b").json()["admin"] is False


def test_repeated_failures_are_slowed_down(server_env):
    with make_client(server_env) as client:
        for _ in range(6):
            login(client, "alice", "mauvais")
        response = login(client, "alice", "motdepasse-a")
        assert response.status_code == 429 and int(response.headers["retry-after"]) >= 1


def test_writes_from_another_origin_are_refused(server_env):
    with make_client(server_env) as client:
        login(client, "alice", "motdepasse-a")
        flow = {"flow": {"name": "x", "nodes": [], "links": []}}
        assert client.post("/api/flows", json=flow, headers={"Origin": "https://piege.exemple.net"}).status_code == 403
        assert client.post("/api/flows", json=flow, headers={"Origin": f"http://{HOST}"}).status_code == 201


def test_forwarded_headers_count_only_from_a_trusted_proxy(server_env, monkeypatch):
    """Derrière un proxy HTTPS de confiance : cookie `Secure` d'après X-Forwarded-Proto ; un en-tête d'identité ne
    vaut jamais rien (seule une session ouvre l'accès)."""
    monkeypatch.setenv("ETL_TRUSTED_PROXIES", "10.0.0.0/24")
    headers = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.9", "X-Forwarded-User": "bob"}
    body = {"username": "alice", "password": "motdepasse-a"}
    with make_client(server_env, client=("10.0.0.7", 50000)) as client:
        assert client.get("/api/auth/me", headers=headers).status_code == 401
        assert "secure" in client.post("/api/auth/login", json=body, headers=headers).headers["set-cookie"].lower()
    with make_client(server_env, client=("192.168.1.9", 50000)) as client:
        assert "secure" not in client.post("/api/auth/login", json=body, headers=headers).headers["set-cookie"].lower()


def test_server_refuses_to_start_without_any_account(tmp_path, monkeypatch, capsys):
    from etl_api.serve import run_server

    monkeypatch.setenv("ETL_USERS_FILE", str(tmp_path / "aucun.json"))
    monkeypatch.delenv("ETL_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("ETL_MODE", "desktop")
    assert run_server(server=True) == 2
    assert "ETL_ADMIN_PASSWORD" in capsys.readouterr().err


def test_desktop_mode_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("ETL_MODE", raising=False)
    with make_client(tmp_path) as client:
        assert client.get("/api/auth/info").json()["mode"] == "desktop"
        me = client.get("/api/auth/me", headers={"X-ETL-User": "dupont"}).json()
        assert (me["name"], me["admin"], me["mode"]) == ("dupont", True, "desktop")
        assert client.post("/api/auth/login", json={"username": "a", "password": "b"}).status_code == 404


def test_machine_settings_are_reserved_to_administrators(server_env):
    with make_client(server_env) as client:
        login(client, "alice", "motdepasse-a")
        assert client.get("/api/settings").json()["server"] == {"admin": False}
        assert client.patch("/api/settings", json={"workers": 2}).status_code == 403
        assert client.put("/api/assistant/default/fake").status_code == 403
    with make_client(server_env) as client:
        login(client, "bob", "motdepasse-b")
        server = client.get("/api/settings").json()["server"]
        assert server["admins"] == ["group:carriole-admins"] and server["read_roots"] == []
        assert client.patch("/api/settings", json={"workers": 2}).status_code == 200


def test_each_user_keeps_their_own_language(server_env):
    from etl_engine import settings as user_settings

    with make_client(server_env) as alice, make_client(server_env) as bob:
        login(alice, "alice", "motdepasse-a")
        login(bob, "bob", "motdepasse-b")
        assert alice.put("/api/preferences/language", json={"language": "en"}).json()["language"] == "en"
        assert alice.get("/api/preferences").json()["language"] == "en"
        assert bob.get("/api/preferences").json()["language"] == "fr"
    assert user_settings.get("language") is None  # la machine n'a pas changé de langue
