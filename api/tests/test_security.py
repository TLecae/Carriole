import pytest
from etl_api.main import create_app
from etl_api.security import content_security_policy
from etl_store import Settings
from fastapi.testclient import TestClient

TOKEN = "jeton-de-test"
HOST = "127.0.0.1:8123"


def make_client(tmp_path, **extra):
    settings = Settings(
        meta_dir=tmp_path / "meta", cache_dir=tmp_path / "cache", plugins_dir=tmp_path / "plugins", **extra
    )
    return TestClient(create_app(settings), base_url=f"http://{HOST}", follow_redirects=False)


@pytest.fixture
def guarded(tmp_path):
    with make_client(tmp_path, api_token=TOKEN, allowed_hosts=(HOST, "localhost:8123")) as client:
        yield client


def test_security_headers_are_always_sent(tmp_path):
    with make_client(tmp_path) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.headers["content-security-policy"] == content_security_policy()
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_extra_tile_servers_extend_the_policy_without_touching_the_code(monkeypatch):
    assert "tuiles.exemple.fr" not in content_security_policy()
    monkeypatch.setenv("ETL_TILE_HOSTS", "https://tuiles.exemple.fr")
    policy = content_security_policy()
    assert "img-src 'self' data: blob: https://tile.openstreetmap.org" in policy
    assert policy.count("https://tuiles.exemple.fr") == 2  # img-src et connect-src


def test_no_token_configured_means_no_control(tmp_path):
    with make_client(tmp_path) as client:
        assert client.get("/api/modifiers").status_code == 200


def test_requests_without_token_are_refused_but_health_is_public(guarded):
    assert guarded.get("/api/health").status_code == 200
    assert guarded.get("/api/modifiers").status_code == 401
    assert guarded.get("/tiles/x/y/z/0/0/0.pbf").status_code == 401


def test_header_token_for_the_command_line(guarded):
    assert guarded.get("/api/modifiers", headers={"X-ETL-Token": TOKEN}).status_code == 200
    assert guarded.get("/api/modifiers", headers={"X-ETL-Token": "autre"}).status_code == 401


def test_login_link_sets_a_strict_httponly_cookie(guarded):
    refused = guarded.get("/?token=faux")
    assert refused.status_code == 401 and "set-cookie" not in refused.headers
    login = guarded.get(f"/?token={TOKEN}")
    assert login.status_code == 303 and login.headers["location"] == "/"
    cookie = login.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert guarded.get("/api/modifiers").status_code == 200  # le client garde le cookie


def test_a_foreign_host_name_is_refused_even_with_the_token(guarded):
    response = guarded.get("/api/modifiers", headers={"Host": "pirate.example", "X-ETL-Token": TOKEN})
    assert response.status_code == 403
