import os
import sqlite3
from urllib.parse import urlsplit

import pytest
from etl_api.main import create_app
from etl_engine import connections as engine_connections
from etl_store import Settings
from fastapi.testclient import TestClient

TEST_PG = os.environ.get("ETL_TEST_PG")


def make_settings(tmp_path):
    return Settings(meta_dir=tmp_path / "meta", cache_dir=tmp_path / "cache", plugins_dir=tmp_path / "plugins")


@pytest.fixture
def client(tmp_path):
    engine_connections.clear_providers()
    with TestClient(create_app(make_settings(tmp_path))) as c:
        c.tmp = tmp_path
        yield c
    engine_connections.clear_providers()


BODY = {"host": "db.exemple", "port": 5432, "dbname": "sig", "user": "lecture", "password": "s3cr3t-mot-de-passe"}


def test_create_list_update_delete_never_returns_password(client):
    created = client.put("/api/connections/base_sig", json=BODY)
    assert created.status_code == 200 and created.json()["has_password"] is True
    assert "s3cr3t" not in created.text
    listing = client.get("/api/connections")
    assert [c["name"] for c in listing.json()] == ["base_sig"] and "s3cr3t" not in listing.text

    # mise à jour sans mot de passe : l'ancien est conservé
    update = {k: v for k, v in BODY.items() if k != "password"} | {"host": "autre.exemple"}
    assert client.put("/api/connections/base_sig", json=update).json()["has_password"] is True
    info = engine_connections.resolve("base_sig")
    assert info.host == "autre.exemple" and info.password == BODY["password"]

    # mot de passe vide : effacé
    assert client.put("/api/connections/base_sig", json=update | {"password": ""}).json()["has_password"] is False
    assert client.delete("/api/connections/base_sig").status_code == 204
    assert client.delete("/api/connections/base_sig").status_code == 404
    assert client.get("/api/connections").json() == []


def test_password_is_encrypted_at_rest(client):
    client.put("/api/connections/ref", json=BODY)
    db = sqlite3.connect(client.tmp / "meta" / "etl.db")
    stored = db.execute("SELECT secret_enc FROM connections WHERE name = 'ref'").fetchone()[0]
    db.close()
    assert stored and BODY["password"] not in stored
    assert (client.tmp / "meta" / "secret.key").exists()


def test_validation(client):
    bad_name = client.put("/api/connections/..%2Fx", json=BODY)
    assert bad_name.status_code in (404, 422)
    assert client.put("/api/connections/ok", json=BODY | {"sslmode": "bizarre"}).status_code == 422
    assert client.put("/api/connections/ok", json=BODY | {"port": 0}).status_code == 422
    assert client.put("/api/connections/ok", json=BODY | {"host": " "}).status_code == 422
    assert client.put("/api/connections/ok", json={"dbname": "x"}).status_code == 422  # hôte absent


def test_lost_key_gives_clear_error(tmp_path):
    engine_connections.clear_providers()
    with TestClient(create_app(make_settings(tmp_path))) as c:
        c.put("/api/connections/ref", json=BODY)
    (tmp_path / "meta" / "secret.key").unlink()  # clé perdue : une nouvelle est générée
    engine_connections.clear_providers()
    with TestClient(create_app(make_settings(tmp_path))) as c:
        assert c.get("/api/connections").json()[0]["has_password"] is True
        with pytest.raises(Exception, match="illisible"):
            engine_connections.resolve("ref")
    engine_connections.clear_providers()


def test_secret_key_from_environment(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet

    key = Fernet.generate_key()
    monkeypatch.setenv("ETL_SECRET_KEY", key.decode())
    assert make_settings(tmp_path).secret_key == key
    assert not (tmp_path / "meta" / "secret.key").exists()


def test_unknown_connection_routes(client):
    assert client.post("/api/connections/nope/test").status_code == 404
    assert client.get("/api/connections/nope/tables").status_code == 404


@pytest.mark.skipif(not TEST_PG, reason="ETL_TEST_PG non défini")
def test_test_and_tables_against_postgis(client):
    parts = urlsplit(TEST_PG)
    body = {
        "host": parts.hostname,
        "port": parts.port or 5432,
        "dbname": parts.path.lstrip("/"),
        "user": parts.username,
        "password": parts.password,
        "sslmode": "disable",
    }
    client.put("/api/connections/pg", json=body)
    ok = client.post("/api/connections/pg/test").json()
    assert ok["ok"] is True and "PostgreSQL" in ok["server"]
    assert isinstance(client.get("/api/connections/pg/tables").json(), list)

    client.put("/api/connections/bad", json=body | {"password": "pwd-faux-x"})
    failed = client.post("/api/connections/bad/test").json()
    assert failed["ok"] is False and "pwd-faux-x" not in failed["error"]

    # le moteur utilise la connexion nommée (analyse d'un flux)
    flow = {
        "name": "pg",
        "nodes": [{"id": "r", "type": "PostgresReader", "params": {"connection": "pg", "query": "SELECT 1 AS un"}}],
        "links": [],
    }
    report = client.post("/api/analyze", json={"flow": flow}).json()
    assert [i for i in report["issues"] if i["level"] == "error"] == []
    assert report["schemas"]["r/output"]["fields"][0]["name"] == "un"


def test_oracle_connections_keep_their_type_and_unknown_types_are_refused(client):
    oracle = client.put("/api/connections/ora", json={"kind": "oracle", "host": "ora", "dbname": "ORCLPDB1"}).json()
    assert (oracle["kind"], oracle["port"], oracle["options"]) == ("oracle", 1521, {})  # port par défaut du type
    assert engine_connections.resolve("ora").kind == "oracle"
    for kind in ("sqlserver", "mysql"):  # SQL Server n'est plus pris en charge
        assert client.put("/api/connections/x", json={"kind": kind, "host": "h", "dbname": "b"}).status_code == 422
    # serveur injoignable : l'essai le dit, par le pilote de la bonne base
    client.put("/api/connections/loin", json={"kind": "oracle", "host": "127.0.0.1", "port": 1, "dbname": "b"})
    result = client.post("/api/connections/loin/test").json()
    assert result["ok"] is False and "impossible" in result["error"]
