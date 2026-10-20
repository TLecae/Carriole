import sqlite3
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from etl_api.main import create_app
from etl_engine import components as engine_components
from etl_engine import connections as engine_connections
from etl_store import Settings, Store
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    engine_components.clear_providers()
    engine_connections.clear_providers()
    settings = Settings(meta_dir=tmp_path / "meta", cache_dir=tmp_path / "cache", plugins_dir=tmp_path / "plugins")
    with TestClient(create_app(settings)) as c:
        c.tmp = tmp_path
        yield c
    engine_components.clear_providers()
    engine_connections.clear_providers()


def component_flow(name="filtre_pop", threshold="1000"):
    return {
        "name": "Filtre par population",
        "description": "Garde les communes d'au moins SEUIL habitants.",
        "component": {"name": name, "label": "Filtre population", "category": "Communes"},
        "published_params": [{"name": "SEUIL", "label": "Seuil", "default": threshold}],
        "nodes": [
            {"id": "in", "type": "ComponentInput", "params": {"port": "in"}},
            {
                "id": "t",
                "type": "ExtractByExpression",
                "params": {"conditions": ['CAST("pop" AS INTEGER) >= ${SEUIL}']},
            },
            {"id": "ok", "type": "ComponentOutput", "params": {"port": "ok"}},
        ],
        "links": [
            {"id": "a", "from": ["in", "output"], "to": ["t", "input"]},
            {"id": "b", "from": ["t", "Passed"], "to": ["ok", "input"]},
        ],
    }


def user_flow(tmp, params=None):
    src = tmp / "communes.parquet"
    pq.write_table(pa.table({"pop": np.arange(0, 2000, 100).astype(str), "nom": [f"c{i}" for i in range(20)]}), src)
    return {
        "name": "Utilisateur",
        "nodes": [
            {"id": "r", "type": "Reader", "params": {"path": str(src)}},
            {"id": "f", "type": "component:filtre_pop", "params": params or {}},
            {"id": "w", "type": "Writer", "params": {"path": str(tmp / "out.parquet")}},
        ],
        "links": [
            {"id": "l1", "from": ["r", "output"], "to": ["f", "in"]},
            {"id": "l2", "from": ["f", "ok"], "to": ["w", "input"]},
        ],
    }


def test_component_appears_in_the_palette_like_a_modifier(client):
    created = client.post("/api/flows", json={"flow": component_flow()}).json()
    assert created["component"] == "filtre_pop"
    palette = client.get("/api/modifiers").json()
    info = next(t for t in palette["modifiers"] if t["name"] == "component:filtre_pop")
    assert info["component"] is True and info["flow_id"] == created["id"]
    assert info["inputs"] == ["in"] and info["outputs"] == ["ok"] and info["category"] == "Communes"
    assert info["params_schema"]["properties"]["SEUIL"]["default"] == "1000"
    assert {"ComponentInput", "ComponentOutput"} <= {t["name"] for t in palette["modifiers"]}
    assert [f for f in client.get("/api/flows").json() if f["component"]] != []


def test_component_names_must_be_unique_and_valid(client):
    assert client.post("/api/flows", json={"flow": component_flow()}).status_code == 201
    twin = client.post("/api/flows", json={"flow": component_flow()})
    assert twin.status_code == 422 and "déjà le composant" in twin.text
    other = client.post("/api/flows", json={"flow": component_flow("autre")}).json()
    renamed = client.put(f"/api/flows/{other['id']}", json={"flow": component_flow("filtre_pop"), "revision": 1})
    assert renamed.status_code == 422
    same = client.put(f"/api/flows/{other['id']}", json={"flow": component_flow("autre", "5"), "revision": 1})
    assert same.status_code == 200  # un flux peut se ré-enregistrer sous son propre nom
    assert client.post("/api/flows", json={"flow": component_flow("nom invalide!")}).status_code == 422


def wait_done(client, run_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] not in ("queued", "running"):
            return run
        time.sleep(0.2)
    raise AssertionError("run non terminé")


def test_analysis_and_run_use_the_component_and_pin_its_version(client):
    from etl_engine.executor import run_flow
    from etl_engine.flow import Flow
    from etl_engine.registry import Registry

    stored = client.post("/api/flows", json={"flow": component_flow()}).json()
    flow = user_flow(client.tmp, {"SEUIL": "1500"})
    report = client.post("/api/analyze", json={"flow": flow}).json()
    assert [i for i in report["issues"] if i["level"] == "error"] == []
    assert report["aliases"] == {"f/ok": "f__t/Passed"}
    assert [f["name"] for f in report["schemas"]["f/ok"]["fields"]] == ["pop", "nom"]

    run = client.post("/api/runs", json={"flow": flow, "cache": True})
    assert run.status_code == 201, run.text
    pinned = run.json()["flow"]
    assert "filtre_pop" in pinned["subflows"]  # version figée dans le run
    # le composant change après coup : le run déjà créé garde l'ancienne version
    client.put(f"/api/flows/{stored['id']}", json={"flow": component_flow(threshold="9999"), "revision": 1})
    assert (
        client.get(f"/api/runs/{run.json()['id']}").json()["flow"]["subflows"]["filtre_pop"]["published_params"][0][
            "default"
        ]
        == "1000"
    )
    # exécution (sans worker) de ce flux figé : mêmes résultats qu'à la création
    res = run_flow(Flow.model_validate(pinned), Registry.discover(), client.tmp / "run")
    assert res.status == "done", res.error
    assert pq.read_table(client.tmp / "out.parquet").num_rows == 5


def test_unknown_component_is_refused_at_run_creation(client):
    flow = user_flow(client.tmp)
    response = client.post("/api/runs", json={"flow": flow})
    assert response.status_code == 422 and "inconnu" in response.text


def test_migration_to_v4_backfills_component_names(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE meta (key VARCHAR(50) PRIMARY KEY, value TEXT);
        INSERT INTO meta VALUES ('schema_version', '3');
        CREATE TABLE flows (id VARCHAR(32) PRIMARY KEY, name VARCHAR(200), content TEXT, owner VARCHAR(100),
          created_at FLOAT, updated_at FLOAT, revision INTEGER);
        """
    )
    db.execute(
        "INSERT INTO flows VALUES ('a', 'comp', ?, 'x', 1, 1, 1)",
        ('{"component": {"name": "mon_composant"}, "nodes": [], "links": []}',),
    )
    db.execute("INSERT INTO flows VALUES ('b', 'simple', '{\"nodes\": []}', 'x', 1, 1, 1)")
    db.commit()
    db.close()
    store = Store(f"sqlite:///{path.as_posix()}")
    assert [c["id"] for c in store.list_components()] == ["a"]
    assert store.get_component("mon_composant")["name"] == "comp"
    assert store.get_component("absent") is None


# ---------------------------------------------------------------------------------- versions


def test_publish_versions_pin_nodes_and_keep_runs_reproducible(client):
    stored = client.post("/api/flows", json={"flow": component_flow()}).json()
    assert client.get("/api/components/filtre_pop/versions").json() == {"versions": [], "draft_changed": True}

    v1 = client.post("/api/components/filtre_pop/versions", json={"note": "première"})
    assert v1.status_code == 201 and v1.json()["version"] == 1 and v1.json()["note"] == "première"
    assert client.post("/api/components/filtre_pop/versions", json={}).status_code == 409  # rien n'a changé
    listing = client.get("/api/components/filtre_pop/versions").json()
    assert [v["version"] for v in listing["versions"]] == [1] and listing["draft_changed"] is False

    # le brouillon change : les flux qui suivent « la dernière » ne bougent pas tant qu'on ne publie pas
    client.put(f"/api/flows/{stored['id']}", json={"flow": component_flow(threshold="1500"), "revision": 1})
    assert client.get("/api/components/filtre_pop/versions").json()["draft_changed"] is True
    flow = user_flow(client.tmp)
    first = client.post("/api/runs", json={"flow": flow}).json()["flow"]
    assert first["subflows"].keys() == {"filtre_pop@1"}
    assert next(n for n in first["nodes"] if n["id"] == "f")["component_version"] == 1

    v2 = client.post("/api/components/filtre_pop/versions", json={"note": "seuil 1500"})
    assert v2.json()["version"] == 2
    palette = client.get("/api/modifiers").json()["modifiers"]
    info = next(t for t in palette if t["name"] == "component:filtre_pop")
    assert info["latest_version"] == 2
    assert info["params_schema"]["properties"]["SEUIL"]["default"] == "1500"

    # nœud sans version : suit la 2 ; nœud épinglé à la 1 : garde la 1
    following = client.post("/api/runs", json={"flow": flow}).json()["flow"]
    assert following["subflows"].keys() == {"filtre_pop@2"}
    pinned = user_flow(client.tmp)
    next(n for n in pinned["nodes"] if n["id"] == "f")["component_version"] = 1
    old = client.post("/api/runs", json={"flow": pinned}).json()["flow"]
    assert old["subflows"].keys() == {"filtre_pop@1"}
    assert old["subflows"]["filtre_pop@1"]["published_params"][0]["default"] == "1000"

    report = client.post("/api/analyze", json={"flow": pinned}).json()
    assert [i for i in report["issues"] if i["level"] == "error"] == []
    unknown = user_flow(client.tmp)
    next(n for n in unknown["nodes"] if n["id"] == "f")["component_version"] = 9
    assert client.post("/api/runs", json={"flow": unknown}).status_code == 422


def test_publish_requires_a_saved_component(client):
    assert client.post("/api/components/absent/versions", json={}).status_code == 404
    assert client.get("/api/components/absent/versions").json() == {"versions": [], "draft_changed": True}
