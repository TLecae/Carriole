"""`etl ai` : interroge l'assistant du backend local ; une modification n'est écrite que si --out est donné."""

import json

from serve_support import make_backend  # noqa: F401 - fixture
from test_cli_backend import etl

SCRIPT = [
    {
        "match": "filtre",
        "turns": [
            {
                "text": "Je propose un filtre.",
                "tool_calls": [
                    {
                        "name": "propose_flow_edits",
                        "input": {
                            "operations": [
                                {"op": "add_node", "type": "ExtractByExpression", "id": "ExtractByExpression_1"}
                            ]
                        },
                    }
                ],
            }
        ],
    }
]
FLOW = {
    "name": "demo",
    "nodes": [{"id": "Reader_1", "type": "Reader", "params": {}, "position": [80, 80]}],
    "links": [],
}


def test_ai_prints_the_answer_and_writes_the_proposed_flow_only_with_out(make_backend, tmp_path):  # noqa: F811
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    providers = {"fake": {"kind": "fake", "label": "Faux", "model": "fake-1", "script": SCRIPT}}
    (home / "config" / "settings.json").write_text(
        json.dumps({"assistant": {"default": "fake", "providers": providers}}), encoding="utf-8"
    )
    make_backend(workers=1, home=home)
    source = tmp_path / "flux.json"
    source.write_text(json.dumps(FLOW), encoding="utf-8")

    plain = etl(home, "ai", "ajoute un filtre", "--flow", str(source), check=True)
    assert "Je propose un filtre." in plain.stdout and "Ajouter ExtractByExpression" in plain.stdout
    assert "--out" in plain.stdout and json.loads(source.read_text(encoding="utf-8")) == FLOW  # rien n'est écrit

    target = tmp_path / "nouveau.json"
    etl(home, "ai", "ajoute un filtre", "--flow", str(source), "--out", str(target), check=True)
    written = json.loads(target.read_text(encoding="utf-8"))
    assert [n["id"] for n in written["nodes"]] == ["Reader_1", "ExtractByExpression_1"]
    assert json.loads(source.read_text(encoding="utf-8")) == FLOW  # le flux d'origine reste intact


def test_ai_without_backend_or_provider_says_so(tmp_path):
    assert "n'est pas démarré" in etl(tmp_path / "home", "ai", "bonjour").stderr
