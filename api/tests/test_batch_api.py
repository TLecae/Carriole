import pytest
from etl_api.main import create_app
from etl_store import Settings
from fastapi.testclient import TestClient

FLOW = {
    "name": "lot",
    "published_params": [{"name": "SRC", "required": True}],
    "nodes": [{"id": "r", "type": "Reader", "params": {"path": "${SRC}"}}],
    "links": [],
}


@pytest.fixture
def client(tmp_path):
    cfg = Settings(meta_dir=tmp_path / "meta", cache_dir=tmp_path / "cache", plugins_dir=tmp_path / "plugins")
    with TestClient(create_app(cfg)) as c:
        yield c


@pytest.fixture
def csvs(tmp_path):
    for name in ("a.csv", "b.csv", "c.csv"):
        (tmp_path / name).write_text("id\n1\n", encoding="utf-8")
    return tmp_path


def test_a_batch_creates_one_run_per_file_grouped_together(client, csvs):
    preview = client.post(
        "/api/runs/batch", json={"flow": FLOW, "param": "SRC", "pattern": str(csvs / "*.csv"), "dry_run": True}
    )
    assert preview.status_code == 201 and [
        v.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for v in preview.json()["values"]
    ] == ["a.csv", "b.csv", "c.csv"]
    assert client.get("/api/runs").json() == []  # aperçu : rien n'est lancé

    created = client.post("/api/runs/batch", json={"flow": FLOW, "param": "SRC", "pattern": str(csvs / "*.csv")})
    assert created.status_code == 201, created.text
    runs = created.json()["runs"]
    assert len(runs) == 3
    listed = client.get("/api/runs").json()
    groups = {r["options"]["batch"]["id"] for r in listed}
    assert groups == {created.json()["batch"]} and sorted(r["options"]["batch"]["index"] for r in listed) == [1, 2, 3]
    assert {r["options"]["batch"]["label"] for r in listed} == {"a.csv", "b.csv", "c.csv"}
    assert sorted(
        r["params"]["SRC"].rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        for r in (client.get(f"/api/runs/{x['id']}").json() for x in runs)
    ) == ["a.csv", "b.csv", "c.csv"]


def test_a_batch_from_a_list_of_values_and_refusals(client, csvs):
    ok = client.post(
        "/api/runs/batch",
        json={"flow": FLOW, "param": "SRC", "values": [str(csvs / "a.csv"), " ", str(csvs / "b.csv")]},
    )
    assert ok.status_code == 201 and len(ok.json()["runs"]) == 2
    assert client.post("/api/runs/batch", json={"flow": FLOW, "param": "NOPE", "values": ["x"]}).status_code == 422
    assert (
        client.post("/api/runs/batch", json={"flow": FLOW, "param": "SRC", "pattern": str(csvs / "*.zzz")}).status_code
        == 422
    )
    assert client.post("/api/runs/batch", json={"flow": FLOW, "param": "SRC", "values": ["x"] * 501}).status_code == 422


def test_a_batch_is_refused_as_a_whole_when_the_flow_has_errors(client, csvs):
    broken = {**FLOW, "nodes": [{"id": "t", "type": "ExtractByExpression", "params": {"conditions": []}}]}
    refused = client.post("/api/runs/batch", json={"flow": broken, "param": "SRC", "values": ["x"]})
    assert refused.status_code == 422
    assert client.get("/api/runs").json() == []
