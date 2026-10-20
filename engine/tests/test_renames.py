"""Flux enregistrés avant le renommage du 2026-10-20 : types, ports et paramètres migrés à la lecture."""

from etl_engine import renames
from etl_engine.flow import Flow
from etl_engine.registry import Registry

OLD = {
    "name": "ancien",
    "nodes": [
        {"id": "r", "type": "CsvReader", "params": {"path": "a.csv"}},
        {"id": "t", "type": "Tester", "params": {"conditions": ['"pop" > 1']}},
        {"id": "s", "type": "CsvReader", "params": {"path": "b.csv"}},
        {
            "id": "m",
            "type": "FeatureMerger",
            "params": {"requestor_keys": ["id"], "supplier_keys": ["id"], "geometry": "supplier"},
        },
        {"id": "c", "type": "Clipper", "params": {"clipper_attributes": ["zone"], "prefix": "clipper_"}},
        {"id": "d", "type": "Dissolver", "params": {}},
    ],
    "links": [
        {"id": "l1", "from": ["r", "output"], "to": ["t", "input"]},
        {"id": "l2", "from": ["t", "Passed"], "to": ["m", "requestor"]},
        {"id": "l3", "from": ["s", "output"], "to": ["m", "supplier"]},
        {"id": "l4", "from": ["m", "Merged"], "to": ["c", "clippee"]},
        {"id": "l5", "from": ["s", "output"], "to": ["c", "clipper"]},
        {"id": "l6", "from": ["c", "Inside"], "to": ["d", "input"]},
    ],
}


def test_an_old_flow_opens_with_todays_names_ports_and_params():
    flow = Flow.model_validate(OLD)
    kinds = {n.id: n.type for n in flow.nodes}
    assert kinds == {
        "r": "CsvReader",
        "t": "ExtractByExpression",
        "s": "CsvReader",
        "m": "LookupJoin",
        "c": "Clip",
        "d": "Dissolve",
    }
    links = {lk.id: (tuple(lk.from_), tuple(lk.to)) for lk in flow.links}
    assert links["l2"][1] == ("m", "input") and links["l3"][1] == ("m", "lookup")
    assert links["l4"] == (("m", "Joined"), ("c", "input")) and links["l5"][1] == ("c", "overlay")
    merger = flow.node("m").params
    assert merger == {"input_keys": ["id"], "lookup_keys": ["id"], "geometry": "lookup"}
    assert flow.node("c").params == {"overlay_attributes": ["zone"], "prefix": "overlay_"}


def test_every_old_name_points_to_a_modifier_that_exists_and_ports_match():
    registry = Registry.discover()
    for old, new in renames.TYPES.items():
        assert new in registry and old not in registry, (old, new)
    for kind, ports in renames.PORTS.items():
        cls = registry.get(kind)
        inputs, outputs = cls.ports(cls.Params())  # ports réels (certains dépendent des paramètres)
        assert set(ports.values()) <= {*inputs, *outputs}, kind


def test_a_flow_with_todays_names_is_returned_unchanged():
    data = {"nodes": [{"id": "a", "type": "Buffer", "params": {}}], "links": []}
    assert renames.migrate(data) is data
