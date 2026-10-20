import pyarrow.parquet as pq
import pytest
from etl_engine import settings
from etl_engine.analysis import analyze
from etl_engine.executor import run_flow
from etl_engine.flow import Flow, expand, expand_late, has_late, late_names, resolve_params, system_values

from conftest import make_flow


def flow_with(variables=None, params=None):
    return Flow.model_validate({"name": "t", "variables": variables or {}, "published_params": params or []})


def test_flow_variables_can_cite_each_other_in_any_order():
    flow = flow_with({"OUT": "${BASE}/sortie", "BASE": "${ROOT}/lot", "N": "3"}, [{"name": "ROOT", "default": "Z:"}])
    values = resolve_params(flow)
    assert values["BASE"] == "Z:/lot" and values["OUT"] == "Z:/lot/sortie" and values["N"] == "3"


def test_circular_or_clashing_variables_are_refused():
    with pytest.raises(ValueError, match="circulaires"):
        resolve_params(flow_with({"A": "${B}", "B": "${A}"}))
    with pytest.raises(ValueError, match="circulaires"):
        resolve_params(flow_with({"A": "${A}x"}))
    with pytest.raises(ValueError, match="déjà un paramètre publié"):
        resolve_params(flow_with({"X": "1"}, [{"name": "X"}]))
    with pytest.raises(ValueError, match="nom de variable invalide"):
        resolve_params(flow_with({"a-b": "1"}))


def test_system_variables_are_available_to_the_run():
    system = system_values("Z:/flux/nuit.json", "run42", "alice")
    values = resolve_params(flow_with(), system=system)
    assert expand("${sys:flow_name}@${sys:run_id}/${sys:user}", values) == "nuit@run42/alice"
    assert expand("${sys:flow_dir}", values).replace("\\", "/") == "Z:/flux"
    assert len(expand("${sys:date}", values)) == 10 and "_" in expand("${sys:timestamp}", values)


def test_unavailable_system_variable_is_explained():
    values = resolve_params(flow_with(), system=system_values())
    with pytest.raises(ValueError, match="variable système indisponible"):
        expand("${sys:flow_dir}", values)


def test_global_variables_come_from_the_settings():
    settings.set_value("variables", {"RACINE": "Z:/sig"})
    try:
        values = resolve_params(flow_with())
        assert expand("${global:RACINE}/a", values) == "Z:/sig/a"
        with pytest.raises(ValueError, match="variable globale inconnue"):
            expand("${global:AUTRE}", values)
    finally:
        settings.unset("variables")


def test_late_variables_are_kept_then_resolved():
    assert expand("a ${X} ${var:DATE}", {"X": "1"}) == "a 1 ${var:DATE}"
    assert has_late({"a": ["x ${var:N}"]}) and not has_late("${X}")
    assert late_names({"a": ["x ${var:N}", "${var:M}"]}) == {"N", "M"}
    assert expand_late({"p": "z ${var:N} ${X}"}, {"N": "7"}) == {"p": "z 7 ${X}"}  # le reste n'est pas retouché
    with pytest.raises(ValueError, match="jamais posée"):
        expand_late("${var:N}", {})


def flow_late(communes, tmp_path, with_setter=True):
    nodes = [
        {"id": "read", "type": "Reader", "params": {"path": str(communes)}},
        {"id": "keep", "type": "ExtractByExpression", "params": {"conditions": ["\"dep\" = '${var:DEP}'"]}},
        {"id": "write", "type": "Writer", "params": {"path": str(tmp_path / "out.parquet")}},
    ]
    links = [("keep", "Passed", "write", "input")]
    if with_setter:
        nodes.insert(
            1,
            {
                "id": "set",
                "type": "SetVariables",
                "params": {
                    "variables": [{"name": "DEP", "attribute": "dep", "pick": "last"}, {"name": "N", "source": "count"}]
                },
            },
        )
        links = [("read", "output", "set", "input"), ("set", "output", "keep", "input"), *links]
    else:
        links = [("read", "output", "keep", "input"), *links]
    return make_flow(nodes, links)


def test_variable_set_from_the_data_is_read_downstream(registry, communes, tmp_path):
    events = []
    res = run_flow(flow_late(communes, tmp_path), registry, tmp_path / "run", on_event=events.append)
    assert res.status == "done", res.error
    # la dernière commune a le département « 09 » (indices impairs) : seules les communes « 09 » passent
    assert pq.read_table(tmp_path / "out.parquet").num_rows == 10
    assert set(pq.read_table(tmp_path / "out.parquet").column("dep").to_pylist()) == {"09"}
    assert any("${var:DEP} = 09" in e.get("message", "") for e in events)
    assert any("${var:N} = 20" in e.get("message", "") for e in events)


def test_analysis_refuses_a_variable_nothing_sets(registry, communes, tmp_path):
    issues = analyze(flow_late(communes, tmp_path, with_setter=False), registry)["issues"]
    bad = [i for i in issues if i["code"] == "var_unset"]
    assert bad and bad[0]["node"] == "keep" and "DEP" in bad[0]["message"]
    assert not [i for i in analyze(flow_late(communes, tmp_path), registry)["issues"] if i["level"] == "error"]


def test_variable_set_after_the_reader_is_not_visible_upstream(registry, communes, tmp_path):
    """Un nœud en amont du setter ne peut pas lire sa variable."""
    nodes = [
        {"id": "read", "type": "Reader", "params": {"path": str(communes)}},
        {"id": "check", "type": "ExtractByExpression", "params": {"conditions": ["'${var:X}' = 'x'"]}},
        {"id": "set", "type": "SetVariables", "params": {"variables": [{"name": "X", "attribute": "dep"}]}},
    ]
    links = [("read", "output", "check", "input"), ("check", "Passed", "set", "input")]
    issues = analyze(make_flow(nodes, links), registry)["issues"]
    assert any(i["code"] == "var_unset" and i["node"] == "check" for i in issues)


def test_setter_default_and_validation(registry, communes, tmp_path):
    nodes = [
        {"id": "read", "type": "Reader", "params": {"path": str(communes), "where": "id > 100"}},
        {
            "id": "set",
            "type": "SetVariables",
            "params": {"variables": [{"name": "A", "attribute": "dep", "default": "zz"}]},
        },
        {"id": "keep", "type": "ExtractByExpression", "params": {"conditions": ["'${var:A}' = 'zz'"]}},
    ]
    links = [("read", "output", "set", "input"), ("set", "output", "keep", "input")]
    res = run_flow(make_flow(nodes, links), registry, tmp_path / "run")
    assert res.status == "done", res.error  # aucune entité : la valeur par défaut est posée
    bad = make_flow(
        [{"id": "s", "type": "SetVariables", "params": {"variables": [{"name": "1x", "attribute": "dep"}]}}], []
    )
    assert "nom de variable invalide" in run_flow(bad, registry, tmp_path / "run2").error
