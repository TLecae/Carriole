import json

import pytest
from etl_api.main import create_app
from etl_engine import components as engine_components
from etl_engine import connections as engine_connections
from etl_store import Settings
from fastapi.testclient import TestClient

FLOW = {
    "name": "Flux d'équipe",
    "published_params": [{"name": "DEP", "default": "31"}],
    "nodes": [{"id": "r", "type": "Reader", "params": {"path": "/data/in/x.csv"}}],
    "links": [],
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ETL_COMPONENTS_DIR", str(tmp_path / "composants"))
    engine_components.clear_providers()
    engine_connections.clear_providers()
    settings = Settings(
        meta_dir=tmp_path / "meta",
        cache_dir=tmp_path / "cache",
        plugins_dir=tmp_path / "plugins",
        flow_storage="files",
    )
    with TestClient(create_app(settings)) as c:
        c.tmp = tmp_path
        yield c
    engine_components.clear_providers()
    engine_connections.clear_providers()


def test_save_open_and_edit_a_flow_file(client):
    path = client.tmp / "partage" / "flux.json"
    created = client.post("/api/flows", json={"flow": FLOW, "path": str(path)})
    assert created.status_code == 201, created.text
    flow = created.json()
    assert path.is_file() and flow["path"] == str(path)

    again = client.post("/api/flows", json={"flow": FLOW, "path": str(path)})
    assert again.status_code == 409 and "existe déjà" in again.text
    assert client.post("/api/flows", json={"flow": FLOW}).status_code == 422  # pas de chemin

    got = client.get(f"/api/flows/{flow['id']}").json()
    assert got["flow"]["nodes"][0]["id"] == "r"
    edited = {**FLOW, "name": "autre", "description": "v2"}
    ok = client.put(f"/api/flows/{flow['id']}", json={"flow": edited, "revision": got["revision"]})
    assert ok.status_code == 200 and ok.json()["name"] == "flux"  # le nom du flux est celui de son fichier
    stale = client.put(f"/api/flows/{flow['id']}", json={"flow": FLOW, "revision": got["revision"]})
    assert stale.status_code == 409  # un collègue (ou un autre onglet) a enregistré entre-temps
    assert json.loads(path.read_text("utf-8"))["description"] == "v2"


def test_open_by_path_recents_and_error_messages(client):
    path = client.tmp / "existant.json"
    path.write_text(json.dumps(FLOW), encoding="utf-8")
    opened = client.post("/api/flows/open", json={"path": str(path)})
    assert opened.status_code == 200 and opened.json()["name"] == "existant"
    assert [f["path"] for f in client.get("/api/flows").json()] == [str(path)]

    assert client.post("/api/flows/open", json={"path": str(client.tmp / "absent.json")}).status_code == 404
    (client.tmp / "casse.json").write_text("{", encoding="utf-8")
    assert client.post("/api/flows/open", json={"path": str(client.tmp / "casse.json")}).status_code == 422
    assert client.get("/api/flows/inconnu").status_code == 404

    assert client.delete(f"/api/flows/{opened.json()['id']}").status_code == 204
    assert client.get("/api/flows").json() == [] and path.is_file()  # retiré des récents, fichier intact


def test_runs_and_schedules_follow_the_file_id(client):
    path = client.tmp / "flux.json"
    flow = client.post("/api/flows", json={"flow": FLOW, "path": str(path)}).json()
    run = client.post("/api/runs", json={"flow_id": flow["id"], "params": {"DEP": "09"}}).json()
    assert run["flow_id"] == flow["id"] and run["flow"]["name"] == "flux"
    assert [r["id"] for r in client.get(f"/api/runs?flow_id={flow['id']}").json()] == [run["id"]]

    schedule = client.post(
        "/api/schedules", json={"name": "Nuit", "flow_id": flow["id"], "cron": "30 2 * * *", "params": {}}
    )
    assert schedule.status_code == 201 and schedule.json()["flow_name"] == "flux"
    path.unlink()  # le fichier disparaît : la planification le dit au lieu de planter
    assert client.post(f"/api/schedules/{schedule.json()['id']}/run").status_code in (404, 422)


def test_palette_lists_components_found_in_the_component_folder(client):
    folder = client.tmp / "composants"
    folder.mkdir()
    comp = {
        "name": "nettoyage",
        "component": {"name": "nettoyage", "label": "Nettoyage", "category": "Équipe"},
        "nodes": [
            {"id": "i", "type": "ComponentInput", "params": {"port": "input"}},
            {"id": "o", "type": "ComponentOutput", "params": {"port": "output"}},
        ],
        "links": [{"id": "l", "from": ["i", "output"], "to": ["o", "input"]}],
    }
    (folder / "nettoyage.json").write_text(json.dumps(comp), encoding="utf-8")
    palette = client.get("/api/modifiers").json()["modifiers"]
    item = next(t for t in palette if t["name"] == "component:nettoyage")
    assert item["category"] == "Équipe" and item["component"] is True

    published = client.post("/api/components/nettoyage/versions", json={"note": "v1"})
    assert published.status_code == 201 and published.json()["version"] == 1
    assert (folder / "nettoyage.v1.json").is_file()
    assert client.post("/api/components/nettoyage/versions", json={}).status_code == 409  # rien n'a changé
    listing = client.get("/api/components/nettoyage/versions").json()
    assert [v["version"] for v in listing["versions"]] == [1] and listing["draft_changed"] is False
