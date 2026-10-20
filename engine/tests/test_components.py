"""Composants : un flux réutilisable comme un modificateur."""

import pyarrow.parquet as pq
import pytest
from etl_engine import components
from etl_engine.analysis import analyze
from etl_engine.components import describe, embed_components, expand_components, find, key_of, ports_of
from etl_engine.executor import run_flow
from etl_engine.flow import Flow
from etl_engine.sdk import ConfigError

from conftest import make_flow


def filter_component(name="filtre_pop") -> Flow:
    """Entrée « in » -> ExtractByExpression (pop >= ${SEUIL}) -> sorties « ok » et « ko »."""
    return Flow.model_validate(
        {
            "name": "Filtre par population",
            "description": "Sépare les communes selon un seuil de population.",
            "component": {"name": name, "label": "Filtre population", "category": "Communes"},
            "published_params": [{"name": "SEUIL", "label": "Seuil", "default": "1000"}],
            "nodes": [
                {"id": "in", "type": "ComponentInput", "params": {"port": "in"}},
                {
                    "id": "t",
                    "type": "ExtractByExpression",
                    "params": {"conditions": ['CAST("pop" AS INTEGER) >= ${SEUIL}']},
                },
                {"id": "ok", "type": "ComponentOutput", "params": {"port": "ok"}},
                {"id": "ko", "type": "ComponentOutput", "params": {"port": "ko"}},
            ],
            "links": [
                {"id": "a", "from": ["in", "output"], "to": ["t", "input"]},
                {"id": "b", "from": ["t", "Passed"], "to": ["ok", "input"]},
                {"id": "c", "from": ["t", "Failed"], "to": ["ko", "input"]},
            ],
        }
    )


@pytest.fixture(autouse=True)
def providers():
    components.clear_providers()
    yield
    components.clear_providers()


def outer_flow(communes, tmp_path, params=None, link_ko=True):
    nodes = [
        {"id": "read", "type": "Reader", "params": {"path": str(communes)}},
        {"id": "f", "type": "component:filtre_pop", "params": params or {}},
        {"id": "w_ok", "type": "Writer", "params": {"path": str(tmp_path / "ok.parquet")}},
        {"id": "w_ko", "type": "Writer", "params": {"path": str(tmp_path / "ko.parquet")}},
    ]
    links = [("read", "output", "f", "in"), ("f", "ok", "w_ok", "input")]
    if link_ko:
        links.append(("f", "ko", "w_ko", "input"))
    return make_flow(nodes, links, subflows={"filtre_pop": filter_component().model_dump(mode="json", by_alias=True)})


def test_ports_and_description_of_a_component():
    comp = filter_component()
    assert ports_of(comp) == (["in"], ["ok", "ko"])
    info = describe("filtre_pop", comp)
    assert info["name"] == "component:filtre_pop" and info["label"] == "Filtre population"
    assert info["category"] == "Communes" and info["inputs"] == ["in"] and info["outputs"] == ["ok", "ko"]
    seuil = info["params_schema"]["properties"]["SEUIL"]
    assert seuil["title"] == "Seuil" and seuil["default"] == "1000"


def test_expansion_replaces_the_node_and_rewires_links(communes, tmp_path):
    flow, aliases = expand_components(outer_flow(communes, tmp_path))
    assert {n.id for n in flow.nodes} == {"read", "f__t", "w_ok", "w_ko"}
    wiring = {(link.from_, link.to) for link in flow.links}
    assert (("read", "output"), ("f__t", "input")) in wiring
    assert (("f__t", "Passed"), ("w_ok", "input")) in wiring and (("f__t", "Failed"), ("w_ko", "input")) in wiring
    assert aliases == {("f", "ok"): ("f__t", "Passed"), ("f", "ko"): ("f__t", "Failed")}


def test_component_parameters_are_substituted_with_defaults_and_overrides(communes, tmp_path):
    default, _ = expand_components(outer_flow(communes, tmp_path))
    assert default.node("f__t").params["conditions"] == ['CAST("pop" AS INTEGER) >= 1000']
    custom, _ = expand_components(outer_flow(communes, tmp_path, params={"SEUIL": "1500"}))
    assert custom.node("f__t").params["conditions"] == ['CAST("pop" AS INTEGER) >= 1500']


def test_component_runs_end_to_end_with_aliased_ports(registry, communes, tmp_path):
    events = []
    res = run_flow(
        outer_flow(communes, tmp_path, params={"SEUIL": "1000"}),
        registry,
        tmp_path / "run",
        on_event=events.append,
    )
    assert res.status == "done", res.error
    assert (
        pq.read_table(tmp_path / "ok.parquet").num_rows == 10 and pq.read_table(tmp_path / "ko.parquet").num_rows == 10
    )
    # les ports du nœud composant sont consultables comme ceux de n'importe quel nœud
    assert (tmp_path / "run" / "f" / "ok.parquet").exists() and (tmp_path / "run" / "f" / "ko.parquet").exists()
    assert any(e["type"] == "counts" and e["node"] == "f" and e["port"] == "ok" and e["count"] == 10 for e in events)


def test_analysis_gives_the_component_node_its_schemas(registry, communes, tmp_path):
    report = analyze(outer_flow(communes, tmp_path), registry)
    assert [i for i in report["issues"] if i["level"] == "error"] == []
    assert [f["name"] for f in report["schemas"]["f/ok"]["fields"]] == ["id", "nom", "pop", "dep"]
    assert report["aliases"]["f/ok"] == "f__t/Passed"


def test_unconnected_output_and_disabled_component(registry, communes, tmp_path):
    res = run_flow(outer_flow(communes, tmp_path, link_ko=False), registry, tmp_path / "run")
    assert res.status == "done" and not (tmp_path / "ko.parquet").exists()
    flow = outer_flow(communes, tmp_path)
    flow.node("f").enabled = False
    expanded, _ = expand_components(flow)
    assert not expanded.node("f__t").enabled


def test_run_until_a_component_node(registry, communes, tmp_path):
    res = run_flow(outer_flow(communes, tmp_path), registry, tmp_path / "run", target="f")
    assert res.status == "done" and res.nodes["f__t"].status == "done" and res.nodes["w_ok"].status == "not_run"


def test_nested_components(registry, communes, tmp_path):
    wrapper = Flow.model_validate(
        {
            "component": {"name": "enveloppe"},
            "nodes": [
                {"id": "in", "type": "ComponentInput", "params": {"port": "entree"}},
                {"id": "inner", "type": "component:filtre_pop", "params": {"SEUIL": "1500"}},
                {"id": "out", "type": "ComponentOutput", "params": {"port": "grandes"}},
            ],
            "links": [
                {"id": "a", "from": ["in", "output"], "to": ["inner", "in"]},
                {"id": "b", "from": ["inner", "ok"], "to": ["out", "input"]},
            ],
        }
    )
    flow = make_flow(
        [
            {"id": "read", "type": "Reader", "params": {"path": str(communes)}},
            {"id": "w", "type": "component:enveloppe", "params": {}},
            {"id": "write", "type": "Writer", "params": {"path": str(tmp_path / "big.parquet")}},
        ],
        [("read", "output", "w", "entree"), ("w", "grandes", "write", "input")],
        subflows={
            "filtre_pop": filter_component().model_dump(mode="json", by_alias=True),
            "enveloppe": wrapper.model_dump(mode="json", by_alias=True),
        },
    )
    expanded, aliases = expand_components(flow)
    assert "w__inner__t" in {n.id for n in expanded.nodes}
    assert expanded.node("w__inner__t").params["conditions"] == ['CAST("pop" AS INTEGER) >= 1500']
    assert aliases[("w", "grandes")] == ("w__inner__t", "Passed")
    res = run_flow(flow, registry, tmp_path / "run")
    assert res.status == "done", res.error
    assert pq.read_table(tmp_path / "big.parquet").num_rows == 5  # pop >= 1500 : 1500…1900


def test_unknown_and_recursive_components(registry, communes, tmp_path):
    flow = make_flow([{"id": "x", "type": "component:inconnu", "params": {}}], [])
    report = analyze(flow, registry)
    assert any("inconnu" in i["message"] for i in report["issues"] if i["level"] == "error")
    loop = Flow.model_validate(
        {
            "component": {"name": "boucle"},
            "nodes": [{"id": "me", "type": "component:boucle", "params": {}}],
        }
    )
    outer = make_flow(
        [{"id": "n", "type": "component:boucle", "params": {}}],
        [],
        subflows={"boucle": loop.model_dump(mode="json", by_alias=True)},
    )
    with pytest.raises(ConfigError, match="récursif"):
        expand_components(outer)
    res = run_flow(outer, registry, tmp_path / "run")
    assert res.status == "error" and "récursif" in res.error


def test_providers_directory_and_embedding(communes, tmp_path, monkeypatch):
    folder = tmp_path / "composants"
    folder.mkdir()
    filter_component().save(folder / "filtre.json")
    monkeypatch.setenv("ETL_COMPONENTS_DIR", str(folder))
    assert find("filtre_pop") is not None and find("autre") is None
    flow = make_flow(
        [
            {"id": "read", "type": "Reader", "params": {"path": str(communes)}},
            {"id": "f", "type": "component:filtre_pop", "params": {}},
        ],
        [("read", "output", "f", "in")],
    )
    embedded = embed_components(flow)
    assert "filtre_pop" in embedded.subflows and not flow.subflows
    monkeypatch.delenv("ETL_COMPONENTS_DIR")
    assert find("filtre_pop", embedded) is not None  # figé dans le flux : plus besoin du fournisseur
    assert find("filtre_pop") is None


def test_custom_provider_replaces_by_key():
    components.install_provider(lambda name, v: (filter_component(name), None) if name == "a" else None, key="x")
    assert find("a") is not None
    components.install_provider(lambda name, v: None, key="x")
    assert find("a") is None


def test_component_flow_runs_alone_with_a_sample_file(registry, communes, tmp_path):
    comp = filter_component()
    comp.node("in").params["sample"] = str(communes)
    res = run_flow(comp, registry, tmp_path / "run")
    assert res.status == "done", res.error
    assert pq.read_table(tmp_path / "run" / "ok" / "output.parquet").num_rows == 10


def test_issues_inside_a_component_are_attached_to_its_node(registry, communes, tmp_path):
    broken = filter_component()
    broken.node("t").params["conditions"] = []  # « au moins une condition » : erreur de configuration interne
    flow = make_flow(
        [
            {"id": "read", "type": "Reader", "params": {"path": str(communes)}},
            {"id": "f", "type": "component:filtre_pop", "params": {}},
        ],
        [("read", "output", "f", "in")],
        subflows={"filtre_pop": broken.model_dump(mode="json", by_alias=True)},
    )
    report = analyze(flow, registry)
    inner = [i for i in report["issues"] if i["node"] == "f"]
    assert inner and all(not i["message"].startswith("f__") for i in inner)
    assert any(i["message"].startswith("t : ") for i in inner)


# ---------------------------------------------------------------------------------- versions


def _versions_provider(published: dict[int, Flow], draft: Flow | None = None):
    def provider(name, version):
        if name != "filtre_pop":
            return None
        if version is not None:
            return (published[version], version) if version in published else None
        if published:
            last = max(published)
            return published[last], last
        return (draft, None) if draft else None

    return provider


def _with_threshold(value: str) -> Flow:
    comp = filter_component()
    comp.published_params[0].default = value
    return comp


def test_node_follows_latest_version_and_embedding_pins_it(communes):
    components.install_provider(_versions_provider({1: _with_threshold("100"), 2: _with_threshold("200")}), key="x")
    flow = make_flow(
        [
            {"id": "read", "type": "Reader", "params": {"path": str(communes)}},
            {"id": "f", "type": "component:filtre_pop", "params": {}},
            {"id": "g", "type": "component:filtre_pop", "component_version": 1, "params": {}},
        ],
        [("read", "output", "f", "in"), ("read", "output", "g", "in")],
    )
    embedded = embed_components(flow)
    assert embedded.node("f").component_version == 2 and embedded.node("g").component_version == 1
    assert set(embedded.subflows) == {"filtre_pop@1", "filtre_pop@2"}
    assert flow.node("f").component_version is None  # le flux d'origine n'est pas modifié
    later = {1: _with_threshold("100"), 2: _with_threshold("200"), 3: _with_threshold("300")}
    components.install_provider(_versions_provider(later), key="x")
    expanded, _ = expand_components(embedded)  # rejoue la version figée, pas la 3
    assert expanded.node("f__t").params["conditions"] == ['CAST("pop" AS INTEGER) >= 200']
    assert expanded.node("g__t").params["conditions"] == ['CAST("pop" AS INTEGER) >= 100']


def test_unpublished_component_uses_its_draft_and_unknown_version_is_left_alone(communes):
    components.install_provider(_versions_provider({}, filter_component()), key="x")
    flow = make_flow(
        [
            {"id": "f", "type": "component:filtre_pop", "params": {}},
            {"id": "h", "type": "component:filtre_pop", "component_version": 9, "params": {}},
        ],
        [],
    )
    embedded = embed_components(flow)
    assert embedded.node("f").component_version is None and "filtre_pop" in embedded.subflows
    assert embedded.node("h").component_version == 9 and "filtre_pop@9" not in embedded.subflows
    expanded, _ = expand_components(embedded)
    assert expanded.node("h").type == "component:filtre_pop"  # introuvable : signalé par l'analyse
    assert any(n.id.startswith("f__") for n in expanded.nodes)


def test_nested_components_are_pinned_inside_published_versions(communes):
    inner = _with_threshold("5")
    outer = Flow.model_validate(
        {
            "component": {"name": "enveloppe"},
            "nodes": [
                {"id": "in", "type": "ComponentInput", "params": {"port": "in"}},
                {"id": "c", "type": "component:filtre_pop", "params": {}},
                {"id": "out", "type": "ComponentOutput", "params": {"port": "ok"}},
            ],
            "links": [
                {"id": "a", "from": ["in", "output"], "to": ["c", "in"]},
                {"id": "b", "from": ["c", "ok"], "to": ["out", "input"]},
            ],
        }
    )

    def provider(name, version):
        if name == "filtre_pop":
            return inner, 4
        return (outer, 7) if name == "enveloppe" else None

    components.install_provider(provider, key="x")
    flow = make_flow([{"id": "e", "type": "component:enveloppe", "params": {}}], [])
    embedded = embed_components(flow)
    assert set(embedded.subflows) == {"enveloppe@7", "filtre_pop@4"}
    assert Flow.model_validate(embedded.subflows["enveloppe@7"]).node("c").component_version == 4
    components.clear_providers()  # tout est dans le flux : plus besoin de fournisseur
    expanded, _ = expand_components(embedded)
    assert expanded.node("e__c__t").params["conditions"] == ['CAST("pop" AS INTEGER) >= 5']


def test_self_referencing_component_does_not_loop():
    selfish = Flow.model_validate(
        {
            "component": {"name": "boucle"},
            "nodes": [{"id": "x", "type": "component:boucle", "params": {}}],
            "links": [],
        }
    )
    components.install_provider(lambda name, v: (selfish, None) if name == "boucle" else None, key="x")
    embedded = embed_components(make_flow([{"id": "b", "type": "component:boucle", "params": {}}], []))
    with pytest.raises(ConfigError, match="récursif"):
        expand_components(embedded)


def test_key_of():
    assert key_of("a", None) == "a" and key_of("a", 3) == "a@3"
