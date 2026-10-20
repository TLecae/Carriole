import json

import pytest
from etl_engine.analysis import analyze
from etl_engine.cli import app
from etl_engine.flow import Flow, expand, resolve_params
from etl_engine.registry import Registry
from typer.testing import CliRunner

from conftest import make_flow


def test_flow_json_is_stable_and_roundtrips(tmp_path):
    flow = make_flow(
        [
            {"id": "a", "type": "Reader", "params": {"z": 1, "a": 2}, "position": [10, 20]},
            {"id": "b", "type": "Writer"},
        ],
        [("a", "output", "b", "input")],
        published_params=[{"name": "P", "default": "x"}],
    )
    text = flow.dumps()
    assert text.endswith("\n") and text == Flow.loads(text).dumps()
    data = json.loads(text)
    assert data["links"][0]["from"] == ["a", "output"] and data["schema_version"] == 1
    flow.save(tmp_path / "f.json")
    assert Flow.load(tmp_path / "f.json") == flow


def test_flow_rejects_duplicate_ids_and_future_versions():
    with pytest.raises(ValueError, match="double"):
        make_flow([{"id": "a", "type": "Reader"}, {"id": "a", "type": "Writer"}], [])
    with pytest.raises(ValueError, match="version"):
        Flow.loads('{"schema_version": 99}')


def test_params_resolution():
    flow = make_flow([], [], published_params=[{"name": "A", "default": "1"}, {"name": "B", "required": True}])
    with pytest.raises(ValueError, match="obligatoires"):
        resolve_params(flow)
    values = resolve_params(flow, {"B": "2"})
    assert {k: v for k, v in values.items() if ":" not in k} == {"A": "1", "B": "2"}  # + sys:… et global:…
    assert expand({"k": ["${A}-x", 3]}, {"A": "1"}) == {"k": ["1-x", 3]}
    with pytest.raises(ValueError):
        expand("${INCONNU}", {})


def test_registry_builtin_and_plugin_dir(tmp_path):
    (tmp_path / "mon_plugin.py").write_text(
        "from etl_engine.sdk import Modifier, Params\n"
        "class MonPlugin(Modifier):\n"
        "    name = 'MonPlugin'\n"
        "    label = 'Mon plugin'\n"
        "    class Params(Params):\n"
        "        x: int = 1\n"
        "    def process(self, batch):\n"
        "        return {'output': batch}\n",
        encoding="utf-8",
    )
    (tmp_path / "casse.py").write_text("raise RuntimeError('plugin cassé')\n", encoding="utf-8")
    reg = Registry.discover(tmp_path)
    assert {"Reader", "Writer", "ExtractByExpression", "AddAttributes", "MonPlugin"} <= {c.name for c in reg.all()}
    assert "casse.py" in reg.errors and "plugin cassé" in reg.errors["casse.py"]
    desc = reg.get("MonPlugin").describe()
    assert desc["inputs"] == ["input"] and desc["outputs"] == ["output", "Rejected"]
    assert desc["params_schema"]["properties"]["x"]["type"] == "integer"


def test_describe_exposes_widgets_for_the_form(registry):
    schema = registry.get("RetainAttributes").describe()["params_schema"]
    assert schema["properties"]["attributes"]["x-etl-widget"] == "attributes"
    creator = registry.get("AddAttributes").describe()["params_schema"]
    assert creator["$defs"]["AttributeSpec"]["properties"]["expression"]["x-etl-widget"] == "expression"


def test_analysis_schemas_and_warnings(registry, communes):
    flow = make_flow(
        [
            {"id": "r", "type": "Reader", "params": {"path": str(communes)}},
            {
                "id": "c",
                "type": "AddAttributes",
                "params": {"attributes": [{"name": "dbl", "expression": '"pop" * 2'}]},
            },
            {"id": "k", "type": "RetainAttributes", "params": {"attributes": ["nom", "dbl"]}},
            {"id": "seul", "type": "RenameAttributes", "params": {"renamings": [{"source": "x", "target": "y"}]}},
        ],
        [("r", "output", "c", "input"), ("c", "output", "k", "input")],
    )
    report = analyze(flow, registry)
    fields = lambda key: [f["name"] for f in report["schemas"][key]["fields"]]  # noqa: E731
    assert fields("r/output") == ["id", "nom", "pop", "dep"]
    assert fields("c/output") == ["id", "nom", "pop", "dep", "dbl"]
    assert fields("k/output") == ["nom", "dbl"] and report["schemas"]["k/output"]["geometry"] is True
    assert report["schemas"]["r/output"]["crs"] == "EPSG:2154"
    codes = {(i["node"], i["code"]) for i in report["issues"]}
    assert ("seul", "unconnected_input") in codes
    order = report["order"]
    assert order.index("r") < order.index("c") < order.index("k")

    flow.node("k").params["attributes"] = ["nom", "inexistant"]
    report = analyze(flow, registry)
    assert any(i["level"] == "error" and i["node"] == "k" and "inexistant" in i["message"] for i in report["issues"])


def test_analysis_detects_cycles_unknown_types_and_missing_params(registry):
    flow = make_flow(
        [
            {"id": "a", "type": "RetainAttributes", "params": {}},
            {"id": "b", "type": "RetainAttributes", "params": {}},
            {"id": "x", "type": "Inconnu"},
            {"id": "m", "type": "RetainAttributes", "params": {"attributes": "pas une liste"}},
        ],
        [("a", "output", "b", "input"), ("b", "output", "a", "input")],
    )
    codes = {i["code"] for i in analyze(flow, registry)["issues"]}
    assert {"cycle", "unknown_type", "invalid_params"} <= codes


def test_cli_run_validate_list_and_new_modifier(registry, communes, tmp_path):
    runner = CliRunner()
    flow = make_flow(
        [
            {"id": "r", "type": "Reader", "params": {"path": "${SRC}"}},
            {"id": "w", "type": "Writer", "params": {"path": str(tmp_path / "o.gpkg"), "layer": "communes"}},
        ],
        [("r", "output", "w", "input")],
        published_params=[{"name": "SRC", "required": True}],
    )
    path = tmp_path / "flow.json"
    flow.save(path)

    bad = runner.invoke(app, ["run", str(path)])
    assert bad.exit_code == 1 and "obligatoires" in bad.output

    ok = runner.invoke(app, ["run", str(path), "-p", f"SRC={communes}"])
    assert ok.exit_code == 0, ok.output
    assert "done" in ok.output and (tmp_path / "o.gpkg").exists()

    val = runner.invoke(app, ["validate", str(path), "-p", f"SRC={communes}", "--json"])
    assert val.exit_code == 0 and json.loads(val.output)["schemas"]["r/output"]["fields"][0]["name"] == "id"

    listing = runner.invoke(app, ["modifiers", "list"])
    assert "AddAttributes" in listing.output

    made = runner.invoke(app, ["new-modifier", "SurfaceCalculator", "--dir", str(tmp_path / "plugins")])
    assert made.exit_code == 0
    assert (tmp_path / "plugins" / "surface_calculator.py").exists()
    reg = Registry.discover(tmp_path / "plugins")
    assert "SurfaceCalculator" in {c.name for c in reg.all()}
    assert runner.invoke(app, ["new-modifier", "SurfaceCalculator", "--dir", str(tmp_path / "plugins")]).exit_code == 3
    assert runner.invoke(app, ["new-modifier", "pas_camel"]).exit_code != 0
