"""Assistant : outils, modifications proposées, boucle d'agent avec un fournisseur scripté, banc d'essai des plugins."""

import json

import pytest
from etl_assistant import edits, plugins
from etl_assistant.agent import run_turn
from etl_assistant.privacy import Privacy, default_mode, from_config
from etl_assistant.prompt import build_system
from etl_assistant.providers import FakeProvider
from etl_assistant.refs import resolve
from etl_assistant.tools import ACTION_NAMES, TOOLS, Toolbox, ToolError
from etl_engine.registry import Registry


class StubBackend:
    """Fichiers, runs et données factices : l'assistant ne touche à rien d'autre."""

    def __init__(self):
        self.registry = Registry.discover()
        self.asked: list[str] = []

    def describe_file(self, path, layer, count):
        self.asked.append(path)
        if path.endswith("absent.gpkg"):
            raise ToolError("fichier introuvable : " + path)
        return {
            "path": path,
            "format": "gpkg",
            "geometry": True,
            "crs": "EPSG:2154",
            "fields": [{"name": "nom", "type": "string"}],
        }

    def list_dir(self, path):
        return {"path": path, "entries": [{"name": "communes.gpkg", "dir": False}]}

    def run_info(self, run_id):
        return {"id": run_id, "status": "failed", "error": "attribut absent : pop", "nodes": {"t": "error"}}

    def run_logs(self, run_id, limit):
        return [{"level": "error", "node": "t", "message": "attribut absent : pop " + "x" * 600}][:limit]

    def port_info(self, run_id, node, port):
        return {"total": 3, "columns": [{"name": "nom", "type": "string"}, {"name": "pop", "type": "int64"}]}

    def port_rows(self, run_id, node, port, limit):
        return [{"nom": "Toulouse", "pop": 500000}, {"nom": "Blagnac", "pop": 25000}][:limit]


FLOW = {
    "name": "demo",
    "nodes": [
        {
            "id": "Reader_1",
            "type": "Reader",
            "label": "",
            "params": {"path": "/data/communes.gpkg"},
            "position": [80, 80],
        },
    ],
    "links": [],
}


@pytest.fixture
def box():
    return Toolbox(StubBackend(), flow=FLOW, run_id="run1")


# ------------------------------------------------------------------ modifications de flux
def test_operations_build_a_new_flow_without_touching_the_open_one(box):
    ops = [
        {"op": "add_node", "type": "ExtractByExpression", "params": {"conditions": ['"pop" > 1000']}},
        {"op": "connect", "from": ["Reader_1", "output"], "to": ["ExtractByExpression_1", "input"]},
        {"op": "set_params", "node": "Reader_1", "params": {"layer": "communes"}},
        {"op": "set_label", "node": "ExtractByExpression_1", "label": "Grandes communes"},
    ]
    new = edits.apply_operations(FLOW, ops, box.registry)
    assert [n["id"] for n in new["nodes"]] == ["Reader_1", "ExtractByExpression_1"]
    assert new["nodes"][0]["params"] == {"path": "/data/communes.gpkg", "layer": "communes"}
    assert new["links"][0]["from"] == ["Reader_1", "output"] and new["nodes"][1]["label"] == "Grandes communes"
    assert new["nodes"][1]["position"][0] > FLOW["nodes"][0]["position"][0]  # placé à droite
    assert len(FLOW["nodes"]) == 1 and FLOW["links"] == []  # le flux d'origine est intact


def test_remove_node_drops_its_links(box):
    flow = edits.apply_operations(
        FLOW,
        [
            {"op": "add_node", "type": "ExtractByExpression"},
            {"op": "connect", "from": ["Reader_1", "output"], "to": ["ExtractByExpression_1", "input"]},
        ],
        box.registry,
    )
    assert (
        edits.apply_operations(flow, [{"op": "remove_node", "node": "ExtractByExpression_1"}], box.registry)["links"]
        == []
    )


@pytest.mark.parametrize(
    ("op", "message"),
    [
        ({"op": "add_node", "type": "Inexistant"}, "modifier inconnu"),
        ({"op": "connect", "from": ["Reader_1", "output"], "to": ["Nope", "input"]}, "nœud introuvable"),
        ({"op": "connect", "from": "Reader_1", "to": ["Reader_1", "input"]}, r"donner \[nœud, port\]"),
        ({"op": "set_params", "node": "Reader_1", "params": 3}, "doit être un objet"),
        ({"op": "teleport"}, "opération inconnue"),
        ({"op": "add_node", "type": "ExtractByExpression", "id": "Reader_1"}, "déjà pris"),
        ({"op": "disconnect", "from": ["Reader_1", "output"], "to": ["Reader_1", "input"]}, "lien introuvable"),
    ],
)
def test_invalid_operations_explain_what_to_fix(box, op, message):
    with pytest.raises(edits.EditError, match=message):
        edits.apply_operations(FLOW, [op], box.registry)


def test_propose_flow_edits_returns_the_result_the_summary_and_the_analysis(box):
    proposal = box.propose(
        "propose_flow_edits",
        {"operations": [{"op": "add_node", "type": "ExtractByExpression"}], "explanation": "filtrer"},
    )
    assert [n["id"] for n in proposal["flow"]["nodes"]] == ["Reader_1", "ExtractByExpression_1"]
    assert proposal["summary"] == ["Ajouter ExtractByExpression"] and proposal["explanation"] == "filtrer"
    assert any(
        i["node"] == "ExtractByExpression_1" for i in proposal["issues"]
    )  # conditions manquantes : signalé, pas masqué
    with pytest.raises(ToolError, match="opération 1"):
        box.propose("propose_flow_edits", {"operations": [{"op": "add_node", "type": "Inexistant"}]})
    # paramètre inventé (« relation » d'un ExtractByLocation) : refusé, le modèle corrige avant que l'utilisateur ne voie
    invented = {"operations": [{"op": "add_node", "type": "ExtractByExpression", "params": {"relation": "x"}}]}
    with pytest.raises(
        ToolError, match=r"paramètres inexistants :\n- ExtractByExpression_1 \(ExtractByExpression\) : relation"
    ):
        box.propose("propose_flow_edits", invented)


# ------------------------------------------------------------------ outils de lecture et confidentialité
def test_read_tools(box):
    names = [t["name"] for t in json.loads(box.call_read("list_modifiers", {"category": "Filtres"}))]
    assert "ExtractByExpression" in names and "Reader" not in names
    assert "params_schema" in box.call_read("describe_modifier", {"name": "ExtractByExpression"})
    with pytest.raises(ToolError, match="modifier inconnu"):
        box.call_read("describe_modifier", {"name": "Zzz"})
    assert json.loads(box.call_read("get_flow", {}))["nodes"][0]["id"] == "Reader_1"
    analysis = json.loads(box.call_read("analyze_flow", {}))
    assert (
        analysis["issues"] == []
        and analysis["schemas"]["Reader_1/output"] is not None
        or "Reader_1/output" in analysis["schemas"]
    )
    assert json.loads(box.call_read("get_run_status", {}))["status"] == "failed"
    logs = json.loads(box.call_read("get_run_logs", {}))
    assert len(logs[0]["message"]) <= 300  # les messages sont tronqués
    with pytest.raises(ToolError):
        box.call_read("propose_flow_edits", {})  # un outil d'action n'est pas exécutable en lecture
    with pytest.raises(ToolError, match="arguments invalides"):
        box.call_read("inspect_file", {})


def test_a_remote_provider_gets_schemas_never_values(box):
    sample = json.loads(box.call_read("get_port_sample", {"node": "t"}))
    assert sample["total"] == 3 and sample["columns"][1]["name"] == "pop"
    assert "non transmises" in sample["rows"] and "Toulouse" not in json.dumps(sample)


def test_a_local_provider_may_get_a_few_rows():
    local = Toolbox(StubBackend(), flow=FLOW, run_id="run1", privacy=Privacy("sample", 1))
    sample = json.loads(local.call_read("get_port_sample", {"node": "t", "rows": 10}))
    assert sample["rows"] == [{"nom": "Toulouse", "pop": 500000}]  # plafonné au réglage de l'utilisateur


def test_privacy_defaults():
    assert default_mode("anthropic", None) == "schema"
    assert default_mode("openai", "https://openrouter.ai/api/v1") == "schema"
    assert default_mode("openai", "http://localhost:11434/v1") == "sample"
    assert from_config({"privacy": "n'importe quoi"}, "openai", None).mode == "schema"
    assert from_config({"sample_rows": 500}, "openai", "http://127.0.0.1:11434/v1").sample_rows == 20


def test_system_prompt_lists_the_catalogue_and_states_the_privacy_mode(box):
    system = build_system("fr", box.registry, Privacy())
    assert (
        "- ExtractByExpression [" in system
        and "never data values" in system
        and "(the interface language is fr)" in system
    )
    assert "sample rows" in build_system("en", box.registry, Privacy("sample", 5))


def test_references_are_resolved_into_summaries(box):
    text = resolve(
        [
            {"kind": "file", "path": "/data/communes.gpkg"},
            {"kind": "port", "node": "t"},
            {"kind": "node", "id": "Reader_1"},
            {"kind": "file", "path": "/data/absent.gpkg"},
        ],
        box,
    )
    assert "@/data/communes.gpkg" in text and "EPSG:2154" in text
    assert "@t.output" in text and "Toulouse" not in text
    assert "@Reader_1" in text and "[illisible : fichier introuvable" in text
    assert resolve([], box) == ""


# ------------------------------------------------------------------ boucle d'agent
SCRIPT = [
    {
        "match": "filtre",
        "turns": [
            {"text": "Je regarde le flux.", "tool_calls": [{"name": "get_flow", "input": {}}]},
            {
                "text": "Je propose d'ajouter un filtre.",
                "tool_calls": [
                    {
                        "name": "propose_flow_edits",
                        "input": {
                            "operations": [
                                {
                                    "op": "add_node",
                                    "type": "ExtractByExpression",
                                    "params": {"conditions": ['"pop" > 1000']},
                                }
                            ]
                        },
                    }
                ],
            },
        ],
    },
    {
        "match": "erreur",
        "turns": [
            {
                "tool_calls": [
                    {"name": "propose_flow_edits", "input": {"operations": [{"op": "add_node", "type": "Faux"}]}}
                ]
            },
            {"text": "Corrigé : j'utilise un vrai modificateur.", "tool_calls": []},
        ],
    },
]


def events_of(provider, box, question, history=None):
    history = [*(history or []), {"role": "user", "content": question}]
    return list(run_turn(provider, "fake-1", "système", history, box))


def test_agent_reads_then_proposes_and_waits_for_the_user(box):
    provider = FakeProvider(SCRIPT)
    events = events_of(provider, box, "ajoute un filtre sur la population")
    kinds = [e["type"] for e in events]
    assert (
        "".join(e["delta"] for e in events if e["type"] == "text")
        == "Je regarde le flux.Je propose d'ajouter un filtre."
    )
    tool = next(e for e in events if e["type"] == "tool")
    assert tool["name"] == "get_flow" and tool["ok"]
    action = next(e for e in events if e["type"] == "action")
    assert action["name"] == "propose_flow_edits" and action["data"]["summary"] == ["Ajouter ExtractByExpression"]
    assert kinds[-1] == "done" and len(provider.calls) == 2  # il s'arrête après la proposition : pas de troisième appel
    saved = [e["message"] for e in events if e["type"] == "message"]
    assert [m["role"] for m in saved] == ["assistant", "user", "assistant", "user"]  # historique complet et valide
    assert saved[1]["content"][0]["type"] == "tool_result" and saved[3]["content"][0]["type"] == "tool_result"
    assert box.flow is FLOW and len(FLOW["nodes"]) == 1  # rien n'a été appliqué


def test_a_rejected_proposal_goes_back_to_the_model_which_corrects_itself(box):
    provider = FakeProvider(SCRIPT)
    events = events_of(provider, box, "une erreur dans le flux")
    result = next(e["message"] for e in events if e["type"] == "message" and e["message"]["role"] == "user")
    assert result["content"][0]["is_error"] and "modifier inconnu" in result["content"][0]["content"]
    assert not [e for e in events if e["type"] == "action"]
    assert "Corrigé" in "".join(e["delta"] for e in events if e["type"] == "text")


def test_history_is_replayed_to_the_model(box):
    provider = FakeProvider(SCRIPT)
    first = events_of(provider, box, "ajoute un filtre")
    history = [{"role": "user", "content": "ajoute un filtre"}] + [
        e["message"] for e in first if e["type"] == "message"
    ]
    events_of(provider, box, "autre chose", history)
    last = provider.calls[-1]["messages"]
    assert len(last) == len(history) + 1 and last[-1]["content"] == "autre chose"


def test_provider_errors_are_reported_not_raised(box):
    from etl_assistant.providers import ProviderError

    class Broken:
        kind = "fake"

        def stream(self, **_):
            raise ProviderError("clé refusée par le fournisseur")
            yield  # pragma: no cover

    events = events_of(Broken(), box, "bonjour")
    assert events == [{"type": "thinking", "step": 0}, {"type": "error", "message": "clé refusée par le fournisseur"}]


def test_tool_catalogue_separates_reading_from_acting():
    assert {"propose_flow_edits", "run_flow", "write_modifier", "create_component"} == ACTION_NAMES
    assert len({t["name"] for t in TOOLS}) == len(TOOLS) == 13
    assert all(t["input_schema"]["type"] == "object" for t in TOOLS)


# ------------------------------------------------------------------ écrire un modificateur
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
GOOD_TEST = """
from etl_engine.testing import run_modifier, table
from plugin_double_value import DoubleValue


def test_double():
    assert run_modifier(DoubleValue, {}, table(v=[1, 2]))["output"].column("v").to_pylist() == [2, 4]
"""


def test_write_modifier_proposal_is_checked_statically_and_never_executed(box, tmp_path):
    marker = tmp_path / "ran"
    evil = f"import pathlib\npathlib.Path({str(marker)!r}).write_text('x')\nclass DoubleValue:\n    pass\n"
    proposal = box.propose("write_modifier", {"name": "DoubleValue", "code": evil, "test_code": GOOD_TEST})
    assert proposal["name"] == "DoubleValue" and not marker.exists()  # la proposition n'exécute rien
    with pytest.raises(ToolError, match="syntaxe"):
        box.propose("write_modifier", {"name": "DoubleValue", "code": "def (", "test_code": GOOD_TEST})
    with pytest.raises(ToolError, match="définir une classe"):
        box.propose("write_modifier", {"name": "Autre", "code": GOOD, "test_code": GOOD_TEST})
    with pytest.raises(ToolError, match="existe déjà"):
        box.propose("write_modifier", {"name": "Reader", "code": GOOD, "test_code": GOOD_TEST})
    with pytest.raises(ToolError, match="CamelCase"):
        box.propose("write_modifier", {"name": "bad name", "code": GOOD, "test_code": GOOD_TEST})


def test_plugin_harness_runs_the_test_in_a_separate_process_then_installs(tmp_path):
    ok = plugins.run_tests("DoubleValue", GOOD, GOOD_TEST)
    assert ok["ok"], ok["output"]
    failing = plugins.run_tests("DoubleValue", GOOD, GOOD_TEST.replace("[2, 4]", "[3, 5]"))
    assert not failing["ok"] and "assert" in failing["output"]
    path = plugins.install("DoubleValue", GOOD, tmp_path / "plugins")
    assert path.name == "double_value.py" and "DoubleValue" in path.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        plugins.install("DoubleValue", GOOD, tmp_path / "plugins")
    registry = Registry.discover(tmp_path / "plugins")
    assert "DoubleValue" in registry  # le plugin installé apparaît dans le catalogue


def test_create_component_needs_boundaries(box):
    ok = box.propose(
        "create_component",
        {
            "name": "Nettoyage",
            "nodes": [
                {"id": "in", "type": "ComponentInput", "params": {"name": "input"}},
                {"id": "out", "type": "ComponentOutput", "params": {"name": "output"}},
            ],
            "links": [{"id": "l1", "from": ["in", "output"], "to": ["out", "input"]}],
        },
    )
    assert ok["flow"]["component"]["name"] == "Nettoyage"
    with pytest.raises(ToolError, match="ComponentInput"):
        box.propose("create_component", {"name": "Vide", "nodes": [{"id": "a", "type": "CreateFeatures"}], "links": []})
