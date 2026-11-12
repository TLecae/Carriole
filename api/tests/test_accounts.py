"""Comptes locaux gérés par l'administrateur (ADR 0058) : création, groupes, mot de passe, suppression, premier
compte."""

from __future__ import annotations

from pathlib import Path

import pytest
from etl_api.auth.accounts import bootstrap_admin, hash_password, write_users
from etl_api.auth.config import EXAMPLE_PASSWORD, AuthConfig
from etl_api.main import create_app
from etl_store import Settings
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("server_store")  # PostgreSQL si ETL_TEST_STORE_URL

HOST = "carriole.exemple.org"


@pytest.fixture
def env(tmp_path, monkeypatch):
    users = tmp_path / "users.json"
    write_users(users, {"chef": {"password": hash_password("mdp-du-chef"), "groups": ["carriole-admins"]}})
    monkeypatch.setenv("ETL_MODE", "server")
    monkeypatch.setenv("ETL_USERS_FILE", str(users))
    for name in ("ETL_ADMINS", "ETL_ADMIN_PASSWORD", "ETL_ADMIN_USER"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def client(tmp_path, app=None) -> TestClient:
    settings = Settings(meta_dir=tmp_path / "meta", cache_dir=tmp_path / "cache", plugins_dir=tmp_path / "plugins")
    return TestClient(app or create_app(settings), base_url=f"http://{HOST}")


def login(c: TestClient, name: str, password: str) -> int:
    return c.post("/api/auth/login", json={"username": name, "password": password}).status_code


def test_administrator_creates_updates_and_deletes_accounts(env):
    with client(env) as chef, client(env, chef.app) as dupont:  # un seul serveur, deux navigateurs
        assert login(chef, "chef", "mdp-du-chef") == 200
        assert chef.get("/api/auth/me").json()["accounts"] is True
        new = {"name": "dupont", "display": "Jean Dupont", "groups": ["sig"], "password": "premier-mdp"}
        assert chef.post("/api/accounts", json=new).json() == {
            "name": "dupont",
            "display": "Jean Dupont",
            "groups": ["sig"],
        }
        assert chef.post("/api/accounts", json={**new, "name": "Dupont"}).status_code == 409  # sans casse
        assert chef.post("/api/accounts", json={**new, "name": "x y"}).status_code == 422
        assert chef.post("/api/accounts", json={**new, "name": "court", "password": "1234"}).status_code == 422
        assert [a["name"] for a in chef.get("/api/accounts").json()] == ["chef", "dupont"]

        assert login(dupont, "dupont", "premier-mdp") == 200
        me = dupont.get("/api/auth/me").json()
        assert (me["display"], me["local"], me["admin"]) == ("Jean Dupont", True, False)
        assert dupont.get("/api/accounts").status_code == 403  # réservé aux administrateurs

        # nouveaux groupes : la session se ferme, la reconnexion donne les nouveaux droits
        assert chef.patch("/api/accounts/dupont", json={"groups": ["carriole-admins"]}).status_code == 200
        assert dupont.get("/api/flows").status_code == 401
        assert login(dupont, "dupont", "premier-mdp") == 200 and dupont.get("/api/auth/me").json()["admin"] is True

        chef.patch("/api/accounts/dupont", json={"password": "reinitialise"})  # réinitialisé par l'administrateur
        assert login(dupont, "dupont", "premier-mdp") == 401 and login(dupont, "dupont", "reinitialise") == 200

        assert chef.delete("/api/accounts/chef").status_code == 409  # jamais son propre compte
        assert chef.delete("/api/accounts/dupont").status_code == 204
        assert dupont.get("/api/flows").status_code == 401 and login(dupont, "dupont", "reinitialise") == 401
        log = chef.get("/api/audit", params={"action": "account"}).json()
        assert {e["action"] for e in log} == {"account.create", "account.update", "account.delete"}
        assert all("premier-mdp" not in str(e) and "reinitialise" not in str(e) for e in log)


def test_users_change_their_own_password(env):
    with client(env) as chef:
        login(chef, "chef", "mdp-du-chef")
        body = {"current": "faux", "new": "nouveau-mdp"}
        assert chef.put("/api/auth/password", json=body).status_code == 403
        assert chef.put("/api/auth/password", json={"current": "mdp-du-chef", "new": "court"}).status_code == 422
        assert chef.put("/api/auth/password", json={**body, "current": "mdp-du-chef"}).status_code == 204
        assert login(chef, "chef", "mdp-du-chef") == 401 and login(chef, "chef", "nouveau-mdp") == 200


def test_first_administrator_comes_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("ETL_MODE", "server")
    monkeypatch.setenv("ETL_USERS_FILE", str(tmp_path / "users.json"))
    monkeypatch.delenv("ETL_ADMINS", raising=False)
    monkeypatch.delenv("ETL_ADMIN_PASSWORD", raising=False)
    assert any("ETL_ADMIN_PASSWORD" in p for p in AuthConfig.load().problems())
    # la valeur du fichier d'exemple est publique : aucun compte, démarrage refusé
    example = (Path(__file__).parents[2] / "docker" / "carriole.env.example").read_text(encoding="utf-8")
    assert f"ETL_ADMIN_PASSWORD={EXAMPLE_PASSWORD}\n" in example
    monkeypatch.setenv("ETL_ADMIN_PASSWORD", EXAMPLE_PASSWORD)
    assert bootstrap_admin(AuthConfig.load()) is None and not (tmp_path / "users.json").exists()
    assert any("fichier d'exemple" in p for p in AuthConfig.load().problems())
    monkeypatch.setenv("ETL_ADMIN_PASSWORD", "mot-de-passe-initial")
    assert AuthConfig.load().problems() == []
    with client(tmp_path) as c:
        assert login(c, "admin", "mot-de-passe-initial") == 200 and c.get("/api/auth/me").json()["admin"] is True
    monkeypatch.setenv("ETL_ADMIN_PASSWORD", "autre-mot-de-passe")  # ensuite, la variable ne change plus rien
    with client(tmp_path) as c:
        assert login(c, "admin", "autre-mot-de-passe") == 401
