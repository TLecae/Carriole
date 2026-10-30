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
        "m": "JoinByField",
        "c": "Clip",
        "d": "Dissolve",
    }
    links = {lk.id: (tuple(lk.from_), tuple(lk.to)) for lk in flow.links}
    assert links["l2"][1] == ("m", "base") and links["l3"][1] == ("m", "join")
    assert links["l4"] == (("m", "Joined"), ("c", "input")) and links["l5"][1] == ("c", "overlay")
    merger = flow.node("m").params
    assert merger == {
        "base_keys": ["id"],
        "join_keys": ["id"],
        "geometry": "join",
        "conflict": "other",
        "on_multiple": "first",
    }
    assert flow.node("c").params == {"overlay_attributes": ["zone"], "prefix": "overlay_"}


def test_every_old_name_points_to_a_modifier_that_exists_and_ports_match():
    registry = Registry.discover()
    merged = {"LookupJoin": "JoinByField", "CountPointsInPolygon": "JoinByLocation", "CheckValidity": "FixGeometries"}
    for old, new in renames.TYPES.items():
        assert merged.get(new, new) in registry and old not in registry, (old, new)
    for kind, ports in [*renames.PORTS.items(), *renames.MERGED_PORTS.items()]:
        cls = registry.get(merged.get(kind, kind))
        inputs, outputs = cls.ports(cls.Params())  # ports réels (certains dépendent des paramètres)
        final = {renames.MERGED_PORTS.get(kind, {}).get(p, p) for p in ports.values()}  # puis ceux de l'accueil
        assert final <= {*inputs, *outputs}, kind


def test_a_flow_with_todays_names_is_returned_unchanged():
    data = {"nodes": [{"id": "a", "type": "Buffer", "params": {}}], "links": []}
    assert renames.migrate(data) is data


def test_flows_of_merged_modifiers_run_with_the_same_results(tmp_path):
    """LookupJoin et CountPointsInPolygon (fondus le 2026-10-28) : un ancien flux donne les mêmes résultats."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    import shapely
    from etl_engine.cache import materialize
    from etl_engine.data import GEOM, with_crs
    from etl_engine.executor import run_flow

    def save(name, geoms, **columns):
        wkb = pa.array([shapely.to_wkb(g) for g in geoms], pa.binary())
        pq.write_table(with_crs(pa.table(columns).append_column(GEOM, wkb), "EPSG:2154"), tmp_path / name)
        return {"path": str(tmp_path / name)}

    areas = save("areas.parquet", [shapely.box(0, 0, 10, 10), shapely.box(10, 0, 20, 10)], code=["A", "B"])
    pts = save("pts.parquet", [shapely.Point(1, 1), shapely.Point(10, 5), shapely.Point(15, 5)], code=["A", "A", "Z"])
    old = {
        "name": "ancien",
        "nodes": [
            {"id": "a", "type": "GeoParquetReader", "params": areas},
            {"id": "p", "type": "GeoParquetReader", "params": pts},
            {"id": "n", "type": "CountPointsInPolygon", "params": {"on_multiple": "first"}},
            {"id": "j", "type": "LookupJoin", "params": {"input_keys": ["code"], "lookup_keys": ["code"]}},
        ],
        "links": [
            {"id": "l1", "from": ["a", "output"], "to": ["n", "areas"]},
            {"id": "l2", "from": ["p", "output"], "to": ["n", "points"]},
            {"id": "l3", "from": ["p", "output"], "to": ["j", "input"]},
            {"id": "l4", "from": ["a", "output"], "to": ["j", "lookup"]},
        ],
    }
    result = run_flow(Flow.model_validate(old), Registry.discover(), tmp_path / "run")
    assert result.status == "done", result.error

    def port(node, name):  # écrit, ou défini par sa requête (ADR 0044) : calculé à la lecture
        return pq.read_table(materialize(tmp_path / "run", node, name))

    counts = port("n", "output")
    assert counts.column("_count").to_pylist() == [2, 1]  # le point sur la limite commune compte une fois
    joined = port("j", "Joined")
    assert joined.num_rows == 2  # un seul polygone « A » repris pour chaque point « A » (« first »)
    assert port("j", "UnjoinedBase").column("code").to_pylist() == ["Z"]
    assert port("j", "UnjoinedJoin").column("code").to_pylist() == ["B"]


def test_renamed_params_and_ports_of_todays_modifiers():
    """Paramètres renommés d'un type d'aujourd'hui (et port d'erreur d'HttpRequest) : l'ancien flux s'ouvre tel quel."""
    data = {
        "nodes": [
            {
                "id": "m",
                "type": "MapValues",
                "params": {"source": "code", "mappings": [{"source": "1", "target": "a"}]},
            },
            {"id": "g", "type": "GeometryToAttribute", "params": {"remove": True}},
            {"id": "h", "type": "HttpRequest", "params": {"url": "https://x/{id}"}},
            {"id": "w", "type": "Buffer", "params": {}},
        ],
        "links": [{"id": "l", "from": ["h", "Failed"], "to": ["w", "input"]}],
    }
    out = renames.migrate(data)
    params = {n["id"]: n["params"] for n in out["nodes"]}
    assert params["m"] == {"attribute": "code", "mappings": [{"source": "1", "target": "a"}]}
    assert params["g"] == {"remove_geometry": True}
    assert out["links"][0]["from"] == ["h", "Rejected"]
    registry = Registry.discover()
    for kind, names in renames.PARAMS.items():
        fields = registry.get(kind).Params.model_fields
        assert all(new in fields and old not in fields for old, new in names.items()), kind
    assert renames.migrate(out) is out  # déjà aux noms d'aujourd'hui
