import json

import pytest
from etl_api.main import create_app
from etl_engine import settings
from etl_store import Settings
from fastapi.testclient import TestClient

BAD_FLOW = {
    "name": "t",
    "nodes": [{"id": "n1", "type": "ExtractByExpression", "params": {"conditions": []}}],
    "links": [],
}


@pytest.fixture
def client(tmp_path):
    cfg = Settings(meta_dir=tmp_path / "meta", cache_dir=tmp_path / "cache", plugins_dir=tmp_path / "plugins")
    with TestClient(create_app(cfg)) as c:
        yield c


def labels(response):
    return {t["name"]: t["label"] for t in response.json()["modifiers"]}


def test_palette_is_french_by_default_and_english_on_request(client):
    assert labels(client.get("/api/modifiers"))["ExtractByExpression"] == "Filtrer par expression"
    en = client.get("/api/modifiers", headers={"Accept-Language": "en-US,en;q=0.9"})
    assert labels(en)["ExtractByExpression"] == "Extract by expression"
    assert en.json()["language"] == "en"
    assert labels(client.get("/api/modifiers?lang=en"))["Reproject"] == "Reproject"
    assert (
        labels(client.get("/api/modifiers", headers={"Accept-Language": "xx"}))["ExtractByExpression"]
        == "Filtrer par expression"
    )


def test_error_responses_are_translated(client):
    fr = client.get("/api/runs/nope")
    en = client.get("/api/runs/nope", headers={"Accept-Language": "en"})
    assert fr.status_code == en.status_code == 404
    assert fr.json()["detail"] == "run introuvable"
    assert en.json()["detail"] == "run not found"


def test_analysis_and_run_creation_follow_the_language(client):
    en = {"Accept-Language": "en"}
    issues = client.post("/api/analyze", json={"flow": BAD_FLOW}, headers=en).json()["issues"]
    assert "at least one condition is required" in [i["message"] for i in issues]
    refused = client.post("/api/runs", json={"flow": BAD_FLOW}, headers=en)
    assert refused.status_code == 422
    assert refused.json()["detail"]["message"] == "the flow contains errors"
    assert any(i["message"] == "at least one condition is required" for i in refused.json()["detail"]["issues"])


def test_a_run_remembers_the_language_it_was_created_in(client):
    flow = {"name": "ok", "nodes": [{"id": "c", "type": "ComponentOutput", "params": {"port": "x"}}], "links": []}
    run = client.post("/api/runs", json={"flow": flow}, headers={"Accept-Language": "en"})
    assert run.status_code == 201, run.text
    assert client.get(f"/api/runs/{run.json()['id']}").json()["options"]["locale"] == "en"


def test_preferences_expose_and_change_the_language(client):
    state = client.get("/api/preferences").json()
    assert state["language"] == "fr" and state["configured"] is False
    assert [lang["code"] for lang in state["languages"]][:2] == ["fr", "en"]
    changed = client.put("/api/preferences/language", json={"language": "en"})
    assert changed.json()["language"] == "en" and changed.json()["configured"] is True
    assert settings.get("language") == "en"
    assert client.get("/api/modifiers").json()["language"] == "en"  # sans en-tête : le réglage s'applique
    refused = client.put("/api/preferences/language", json={"language": "klingon"}, headers={"Accept-Language": "en"})
    assert refused.status_code == 422 and "unknown language" in refused.json()["detail"]
    settings.unset("language")


def test_workers_translate_the_run_log(tmp_path):
    from etl_engine.executor import run_flow
    from etl_engine.flow import Flow
    from etl_engine.registry import Registry

    events = []
    run_flow(Flow.model_validate(BAD_FLOW), Registry.discover(), tmp_path, on_event=events.append, lang="en")
    assert json.dumps(events).count("at least one condition") >= 1
