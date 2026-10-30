"""Réglages modifiables depuis l'interface : validation, effet sur settings.json, redémarrage signalé."""

import os
from unittest.mock import patch

import pytest
from etl_api.main import create_app
from etl_engine import settings
from etl_store import Settings
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ETL_HOME", str(tmp_path / "home"))
    cfg = Settings(meta_dir=tmp_path / "meta", cache_dir=tmp_path / "cache", plugins_dir=tmp_path / "plugins")
    with TestClient(create_app(cfg)) as c:
        yield c


def test_defaults_and_paths(client):
    state = client.get("/api/settings").json()
    assert state["values"]["workers"] is None and state["defaults"]["workers"] == 3
    assert state["values"]["component_dirs"] == [] and state["values"]["variables"] == {}
    assert state["paths"]["settings"].endswith("settings.json") and state["version"]


def test_patch_changes_only_the_sent_fields_and_flags_what_needs_a_restart(client, tmp_path):
    shared = tmp_path / "composants"
    shared.mkdir()
    done = client.patch(
        "/api/settings",
        json={"workers": 4, "component_dirs": [str(shared), str(tmp_path / "absent")], "variables": {"BASE": "Z:/x"}},
    ).json()
    assert done["restart_required"] == ["workers"]
    assert done["component_dirs_found"] == {str(shared): True, str(tmp_path / "absent"): False}
    assert settings.get("workers") == 4 and settings.get("variables") == {"BASE": "Z:/x"}

    again = client.patch("/api/settings", json={"workers": 4}).json()
    assert again["restart_required"] == []  # même valeur : rien à redémarrer
    assert settings.get("variables") == {"BASE": "Z:/x"}  # champ non envoyé : inchangé

    reset = client.patch("/api/settings", json={"workers": None, "variables": None}).json()
    assert reset["values"]["workers"] is None and reset["values"]["variables"] == {}
    assert settings.get("workers") is None and settings.get("variables") is None


def test_keep_running_is_saved_for_the_desktop_application(client):
    """Lu par l'application de bureau dans settings.json (desktop/src/backend.ts) ; absent : choix de l'installeur."""
    assert client.get("/api/settings").json()["values"]["keep_running"] is None
    assert client.patch("/api/settings", json={"keep_running": False}).json()["values"]["keep_running"] is False
    assert settings.get("keep_running") is False  # « non » explicite : pas la valeur de l'installeur
    assert client.patch("/api/settings", json={"keep_running": True}).json()["restart_required"] == []
    client.patch("/api/settings", json={"keep_running": None})
    assert settings.get("keep_running") is None


@pytest.mark.parametrize(
    "body",
    [
        {"workers": 0},
        {"workers": 500},
        {"memory_budget_mb": 10},
        {"proxy": "proxy.local:3128"},
        {"tile_hosts": ["https://tuiles.example.org/path"]},
        {"tile_hosts": ["https://a.example.org; script-src *"]},
        {"variables": {"1mauvais": "x"}},
        {"variables": {"a b": "x"}},
        {"cache_dir": "cache"},  # chemin relatif
        {"inconnu": 1},
    ],
)
def test_invalid_values_are_refused_and_change_nothing(client, body):
    assert client.patch("/api/settings", json=body).status_code == 422
    assert settings.load() == {}


def test_tile_hosts_reach_the_content_security_policy(client):
    client.patch("/api/settings", json={"tile_hosts": ["https://tuiles.example.org/"]})
    assert "https://tuiles.example.org" in client.get("/api/health").headers["content-security-policy"]


def test_proxy_and_memory_budget_are_exported_to_the_environment(client):
    with patch.dict(os.environ):  # l'environnement du processus de test est restauré en sortie
        for name in ("HTTPS_PROXY", "HTTP_PROXY", "ETL_MEMORY_BUDGET_MB"):
            os.environ.pop(name, None)
        body = {"proxy": "http://proxy.local:3128", "memory_budget_mb": 2048}
        assert client.patch("/api/settings", json=body).json()["restart_required"] == ["memory_budget_mb", "proxy"]
        settings.apply_environment()
        assert os.environ["HTTPS_PROXY"] == "http://proxy.local:3128"
        assert os.environ["ETL_MEMORY_BUDGET_MB"] == "2048"


def test_cache_folder_is_exported_to_the_environment(client, tmp_path):
    with patch.dict(os.environ):
        os.environ.pop("ETL_CACHE_DIR", None)
        folder = str(tmp_path / "cache")
        state = client.patch("/api/settings", json={"cache_dir": folder}).json()
        assert state["restart_required"] == ["cache_dir"] and state["values"]["cache_dir"] == folder
        settings.apply_environment()
        assert os.environ["ETL_CACHE_DIR"] == folder
        assert client.get("/api/settings").json()["paths"]["cache"] == folder
