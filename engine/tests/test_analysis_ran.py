"""Analyse : un port dont le schéma n'est connu qu'à l'exécution prend celui de ses données au dernier run."""

import pyarrow.parquet as pq
from etl_engine.analysis import analyze
from etl_engine.cache import materialize, port_schema
from etl_engine.executor import run_flow

from conftest import make_flow


def pivot_flow(src):
    nodes = [
        {"id": "read", "type": "Reader", "params": {"path": str(src)}},
        {
            "id": "pivot",
            "type": "PivotWider",
            "params": {"pivot_attribute": "dep", "value_attribute": "pop", "function": "sum", "prefix": "pop_"},
        },
        {
            "id": "calc",
            "type": "AddAttributes",
            "params": {"attributes": [{"name": "total", "expression": '"pop_31" + "pop_09"'}]},
        },
    ]
    return make_flow(nodes, [("read", "output", "pivot", "input"), ("pivot", "output", "calc", "input")])


def names(report, port):
    schema = report["schemas"][port]
    return None if schema is None else [f["name"] for f in schema["fields"]]


def test_unknown_schema_comes_from_the_last_run(registry, communes, tmp_path):
    flow = pivot_flow(communes)
    assert names(analyze(flow, registry), "pivot/output") is None  # valeurs non listées : inconnu avant le run
    run_dir = tmp_path / "run"
    assert run_flow(flow, registry, run_dir).status == "done"

    report = analyze(flow, registry, ran=lambda node, port: port_schema(run_dir, node, port))
    assert names(report, "pivot/output") == ["pop_09", "pop_31"]
    assert names(report, "calc/output") == ["pop_09", "pop_31", "total"]  # l'aval est calculé à partir du cache
    assert not [i for i in report["issues"] if i["code"] == "missing_attribute"]


def test_port_schema_reads_every_port_form(registry, communes, tmp_path):
    run_dir = tmp_path / "run"
    run_flow(pivot_flow(communes), registry, run_dir)
    for node in ("read", "pivot", "calc"):
        found = port_schema(run_dir, node, "output").names  # avant `materialize` : la définition SQL, s'il en a une
        assert found == pq.read_schema(materialize(run_dir, node, "output")).names
    assert port_schema(run_dir, "absent", "output") is None
    assert port_schema(tmp_path / "purged", "pivot", "output") is None
