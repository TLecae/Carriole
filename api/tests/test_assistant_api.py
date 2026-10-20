"""API de l'assistant : fournisseurs et clés, conversations, messages en flux, installation d'un modificateur."""

import json

import pytest
from etl_api.main import create_app
from etl_engine import settings
from etl_store import Settings
from fastapi.testclient import TestClient
from sqlalchemy import text

FLOW = {
    "name": "demo",
    "nodes": [{"id": "Reader_1", "type": "Reader", "params": {"path": "/data/x.csv"}, "position": [80, 80]}],
    "links": [],
}
SCRIPT = [
    {
        "match": "filtre",
        "turns": [
            {"text": "Je regarde.", "tool_calls": [{"name": "get_flow", "input": {}}]},
            {
                "text": "Voici ma proposition.",
                "tool_calls": [
                    {
                        "name": "propose_flow_edits",
                        "input": {"operations": [{"op": "add_node", "type": "ExtractByExpression"}]},
                    }
                ],
            },
        ],
    },
    {
        "match": "fichier",
        "turns": [{"tool_calls": [{"name": "inspect_file", "input": {"path": "{CSV}"}}]}, {"text": "C'est un CSV."}],
    },
    {"match": "hello", "turns": [{"text": "Bonjour !"}]},
]
SECRET = "sk-ant-ceci-est-un-secret-de-test"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ETL_HOME", str(tmp_path / "home"))
    cfg = Settings(meta_dir=tmp_path / "meta", cache_dir=tmp_path / "cache", plugins_dir=tmp_path / "plugins")
    csv = tmp_path / "lieux.csv"
    csv.write_text("nom;pop\nA;1\nB;2\n", encoding="utf-8")
    script = json.loads(json.dumps(SCRIPT).replace("{CSV}", str(csv).replace("\\", "\\\\")))
    settings.set_value(
        "assistant.providers", {"fake": {"kind": "fake", "label": "Faux", "model": "fake-1", "script": script}}
    )
    settings.set_value("assistant.default", "fake")
    with TestClient(create_app(cfg)) as c:
        c.csv = csv
        yield c


def events(response) -> list[dict]:
    out = []
    for block in response.text.split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "data" in lines:
            out.append(json.loads(lines["data"]))
    return out


def new_chat(client) -> str:
    return client.post("/api/assistant/chats", json={"provider": "fake"}).json()["id"]


def send(client, chat, text, **extra):
    return client.post(f"/api/assistant/chats/{chat}/messages", json={"text": text, "flow": FLOW, **extra})


def test_config_lists_providers_presets_and_never_the_key(client):
    config = client.get("/api/assistant/config").json()
    assert (
        config["default"] == "fake" and config["providers"][0]["id"] == "fake" and not config["providers"][0]["has_key"]
    )
    assert {p["id"] for p in config["presets"]} >= {"anthropic", "openai", "openrouter", "ollama"}
    saved = client.put(
        "/api/assistant/providers/claude",
        json={"kind": "anthropic", "label": "Claude", "model": "m", "api_key": SECRET},
    )
    assert saved.status_code == 200 and saved.json()["has_key"] and saved.json()["privacy"] == "schema"
    assert SECRET not in client.get("/api/assistant/config").text and SECRET not in saved.text
    stored = client.app.state.store.engine.connect().execute(text("select secret_enc from assistant_keys")).scalar()
    assert stored and SECRET not in stored  # chiffrée en base
    assert client.app.state.chats.get_key("claude") == SECRET
    assert SECRET not in (settings.app_dirs().settings_file.read_text(encoding="utf-8"))  # ni dans settings.json
    kept = client.put("/api/assistant/providers/claude", json={"kind": "anthropic", "model": "m2"})
    assert kept.json()["has_key"]  # api_key absent : la clé est conservée
    assert not client.put("/api/assistant/providers/claude", json={"kind": "anthropic", "api_key": ""}).json()[
        "has_key"
    ]


def test_local_models_default_to_sharing_samples_remote_ones_to_schemas(client):
    local = client.put(
        "/api/assistant/providers/ollama", json={"kind": "openai", "base_url": "http://localhost:11434/v1"}
    )
    remote = client.put(
        "/api/assistant/providers/or", json={"kind": "openai", "base_url": "https://openrouter.ai/api/v1"}
    )
    assert local.json()["privacy"] == "sample" and remote.json()["privacy"] == "schema"


@pytest.mark.parametrize(
    ("provider_id", "body", "status"),
    [
        ("Bad Id", {"kind": "openai"}, 422),
        ("x", {"kind": "inconnu"}, 422),
        ("x", {"kind": "openai", "base_url": "ftp://x"}, 422),
        ("x", {"kind": "openai", "privacy": "tout"}, 422),
    ],
)
def test_invalid_provider_settings_are_refused(client, provider_id, body, status):
    assert client.put(f"/api/assistant/providers/{provider_id}", json=body).status_code == status


def test_a_provider_without_key_or_unknown_is_refused_clearly(client):
    client.put("/api/assistant/providers/claude", json={"kind": "anthropic", "model": "m"})
    assert client.post("/api/assistant/chats", json={"provider": "claude"}).status_code == 409
    assert client.post("/api/assistant/chats", json={"provider": "nope"}).status_code == 404
    assert client.get("/api/assistant/providers/fake/models").json() == {"models": ["fake-1"]}


def test_a_conversation_streams_reads_the_flow_and_proposes_without_applying(client):
    chat = new_chat(client)
    response = send(client, chat, "ajoute un filtre sur la population")
    assert response.headers["content-type"].startswith("text/event-stream")
    evs = events(response)
    assert "".join(e["delta"] for e in evs if e["type"] == "text") == "Je regarde.Voici ma proposition."
    assert next(e for e in evs if e["type"] == "tool")["name"] == "get_flow"
    action = next(e for e in evs if e["type"] == "action")
    assert action["data"]["summary"] == ["Ajouter ExtractByExpression"] and len(action["data"]["flow"]["nodes"]) == 2
    assert evs[-1]["type"] == "done"
    saved = client.get(f"/api/assistant/chats/{chat}").json()
    assert saved["title"].startswith("ajoute un filtre") and [m["role"] for m in saved["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    assert saved["messages"][0]["ui"]["text"] == "ajoute un filtre sur la population"
    assert client.get("/api/assistant/chats").json()[0]["id"] == chat


def test_the_next_message_replays_a_valid_history_and_carries_the_outcome_of_the_proposal(client):
    chat = new_chat(client)
    send(client, chat, "ajoute un filtre")
    second = events(send(client, chat, "hello", notes=["Proposition appliquée par l'utilisateur."]))
    assert "".join(e["delta"] for e in second if e["type"] == "text") == "Bonjour !"
    messages = client.get(f"/api/assistant/chats/{chat}").json()["messages"]
    assert "Proposition appliquée" in messages[-2]["content"] and messages[-1]["role"] == "assistant"


def test_references_and_file_inspection_go_through_the_allowed_paths(client):
    chat = new_chat(client)
    evs = events(send(client, chat, "décris ce fichier", references=[{"kind": "file", "path": str(client.csv)}]))
    assert not [e for e in evs if e["type"] == "error"]
    saved = client.get(f"/api/assistant/chats/{chat}").json()["messages"]
    assert "References attached by the user" in saved[0]["content"] and "pop" in saved[0]["content"]
    assert saved[0]["ui"]["references"][0]["kind"] == "file"


def test_tool_results_reach_the_model(client):
    chat = new_chat(client)
    send(client, chat, "inspecte ce fichier")
    saved = client.get(f"/api/assistant/chats/{chat}").json()["messages"]
    result = next(b for m in saved if m["role"] == "user" and isinstance(m["content"], list) for b in m["content"])
    assert result["type"] == "tool_result" and "nom" in result["content"] and "pop" in result["content"]


def test_conversations_are_private_to_their_owner_and_deletable(client):
    chat = new_chat(client)
    assert client.get(f"/api/assistant/chats/{chat}", headers={"X-ETL-User": "autre"}).status_code == 404
    assert client.delete(f"/api/assistant/chats/{chat}").status_code == 204
    assert client.get(f"/api/assistant/chats/{chat}").status_code == 404
    assert send(client, chat, "hello").status_code == 404


GOOD = """
import pyarrow.compute as pc
from etl_engine.sdk import Params, Modifier


class DoubleValue(Modifier):
    name = "DoubleValue"
    label = "Double"
    category = "Calculs"
    description = "Double la colonne v."

    class Params(Params):
        pass

    def process(self, batch):
        return {"output": batch.set_column(0, "v", pc.multiply(batch.column("v"), 2))}
"""
TEST = """
from etl_engine.testing import run_modifier, table
from plugin_double_value import DoubleValue


def test_double():
    assert run_modifier(DoubleValue, {}, table(v=[1, 2]))["output"].column("v").to_pylist() == EXPECTED
"""


def test_a_modifier_is_tested_in_isolation_then_installed_and_listed(client):
    failing = client.post(
        "/api/assistant/modifiers",
        json={"name": "DoubleValue", "code": GOOD, "test_code": TEST.replace("EXPECTED", "[9]")},
    )
    assert failing.json()["installed"] is False and "assert" in failing.json()["output"]
    assert not (client.app.state.settings.plugins_dir / "double_value.py").exists()
    ok = client.post(
        "/api/assistant/modifiers",
        json={"name": "DoubleValue", "code": GOOD, "test_code": TEST.replace("EXPECTED", "[2, 4]")},
    )
    assert ok.json()["installed"] is True, ok.json()
    assert "DoubleValue" in {t["name"] for t in client.get("/api/modifiers").json()["modifiers"]}
    again = client.post("/api/assistant/modifiers", json={"name": "DoubleValue", "code": GOOD, "test_code": TEST})
    assert again.status_code == 409
    assert (
        client.post("/api/assistant/modifiers", json={"name": "bad name", "code": GOOD, "test_code": TEST}).status_code
        == 422
    )
