"""Voie SQL (DuckDB) et voie Python : mêmes résultats, la voie SQL étant utilisée quand elle est possible."""

import json
from collections import Counter

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely
from etl_engine.cache import materialize, sql_path
from etl_engine.data import GEOM, get_crs, with_crs
from etl_engine.executor import run_flow

from conftest import make_flow


@pytest.fixture
def people(tmp_path):
    n = 50
    rng = np.random.default_rng(7)
    t = pa.table(
        {
            "id": pa.array(range(n)),
            "dep": pa.array([None if i % 17 == 0 else ["31", "09", "65"][i % 3] for i in range(n)]),
            "pop": pa.array([None if i % 11 == 0 else int(v) for i, v in enumerate(rng.integers(0, 500, n))]),
            "nom": pa.array([f"n{i % 7}" for i in range(n)]),
            GEOM: pa.array(shapely.to_wkb(shapely.points(np.arange(n, dtype=float), np.zeros(n))), pa.binary()),
        }
    )
    path = tmp_path / "people.parquet"
    pq.write_table(with_crs(t, "EPSG:2154"), path)
    return path


@pytest.fixture
def refs(tmp_path):
    t = pa.table(
        {
            "dep": pa.array(["31", "09", "09", "99", None]),
            "label": pa.array(["haute-garonne", "ariège", "ariège bis", "ailleurs", "vide"]),
            "nom": pa.array(["r0", "r1", "r2", "r3", "r4"]),
        }
    )
    path = tmp_path / "refs.parquet"
    pq.write_table(t, path)
    return path


ROUTES = ("written", "defined")  # voie SQL : ports lus par un nœud (écrits), ou par personne (définis, ADR 0044)


def run_both(registry, tmp_path, nodes, links, out_nodes):
    """Exécute le même flux par la voie par lots (`False`) et par la voie SQL (`ROUTES`) ; renvoie ({nœud.port:
    table}, route inscrite dans meta.json)."""
    results = {}
    for route in (False, *ROUTES):
        run_dir = tmp_path / f"run_{route}"
        readers = [(f"c{i}", key.split(".")) for i, key in enumerate(out_nodes)] if route == "written" else []
        flow = make_flow(
            [*nodes, *({"id": c, "type": "Counter", "params": {}} for c, _ in readers)],
            [*links, *((node, port, c, "input") for c, (node, port) in readers)],
        )
        res = run_flow(flow, registry, run_dir, sql_route=bool(route))
        assert res.status == "done", res.error
        for key in out_nodes:
            node, port = key.split(".")
            assert sql_path(run_dir / node / f"{port}.parquet").is_file() == (route == "defined"), key
        results[route] = {key: pq.read_table(materialize(run_dir, *key.split("."))) for key in out_nodes}
        meta = json.loads((run_dir / out_nodes[0].split(".")[0] / "meta.json").read_text(encoding="utf-8"))
        results[(route, "route")] = meta.get("route")
    return results


def same(a: pa.Table, b: pa.Table):
    assert a.column_names == b.column_names
    assert [str(f.type) for f in a.schema] == [str(f.type) for f in b.schema]
    assert a.to_pylist() == b.to_pylist()
    assert get_crs(a) == get_crs(b)


SORTS = [
    [{"attribute": "dep"}, {"attribute": "pop", "order": "descending"}],
    [{"attribute": "nom", "kind": "text"}, {"attribute": "id", "order": "descending"}],
    [{"attribute": "pop", "kind": "number"}],
]


@pytest.mark.parametrize("keys", SORTS)
def test_sorter_same_result_on_both_routes(registry, people, tmp_path, keys):
    nodes = [
        {"id": "r", "type": "Reader", "params": {"path": str(people)}},
        {"id": "s", "type": "OrderBy", "params": {"keys": keys}},
    ]
    out = run_both(registry, tmp_path, nodes, [("r", "output", "s", "input")], ["s.output"])
    for route in ROUTES:
        same(out[False]["s.output"], out[route]["s.output"])
    assert out[("written", "route")] == "sql" and out[("defined", "route")] == "deferred"
    assert out[(False, "route")] is None


@pytest.mark.parametrize("attach", [True, False])
@pytest.mark.parametrize("group_by", [["dep"], []])
def test_statistics_same_result_on_both_routes(registry, people, tmp_path, attach, group_by):
    params = {
        "group_by": group_by,
        "attributes": ["pop"],
        "functions": ["count", "sum", "mean", "min", "max", "median", "distinct"],
        "attach": attach,
    }
    nodes = [
        {"id": "r", "type": "Reader", "params": {"path": str(people)}},
        {"id": "s", "type": "StatisticsByGroup", "params": params},
    ]
    out = run_both(registry, tmp_path, nodes, [("r", "output", "s", "input")], ["s.output", "s.summary"])
    key = (lambda r: str(r.get("dep"))) if group_by else (lambda r: "")
    for route in ROUTES:
        same(out[False]["s.output"], out[route]["s.output"])
        sa = sorted(out[False]["s.summary"].to_pylist(), key=key)
        sb = sorted(out[route]["s.summary"].to_pylist(), key=key)
        assert sa == sb
    assert out[("written", "route")] == "sql" and out[("defined", "route")] == "deferred"


def test_statistics_keep_the_order_of_a_file_read_in_parallel(registry, tmp_path):
    """Les entités sortent dans l'ordre du fichier, même lu par plusieurs threads et bloc par bloc (le rattachement des
    statistiques se fait par position, sans trier les entités)."""
    n = 200_000
    path = tmp_path / "many.parquet"
    t = pa.table({"id": pa.array(range(n)), "dep": pa.array([str(i * 7919 % 97) for i in range(n)])})
    pq.write_table(t, path, row_group_size=5_000)
    stats = {"group_by": ["dep"], "attributes": [], "functions": []}
    nodes = [
        {"id": "r", "type": "Reader", "params": {"path": str(path)}},
        {"id": "s", "type": "StatisticsByGroup", "params": stats},
    ]
    out = run_both(registry, tmp_path, nodes, [("r", "output", "s", "input")], ["s.output"])
    deps = t.column("dep").to_pylist()
    counts = Counter(deps)
    for route in (False, *ROUTES):
        table = out[route]["s.output"]
        assert table.column("id").to_pylist() == list(range(n))
        assert table.column("_count").to_pylist() == [counts[d] for d in deps]


JOINS = [
    {},
    {"on_multiple": "first"},
    {"on_multiple": "first", "prefix": "r_"},
    {"on_multiple": "first", "conflict": "base", "attributes": ["nom", "label"]},
    {"keep_unjoined": True},
    {"keep_unjoined": True, "on_multiple": "first", "attributes": ["label"]},
]


@pytest.mark.parametrize("extra", JOINS)
def test_joiner_same_result_on_both_routes(registry, people, refs, tmp_path, extra):
    nodes = [
        {"id": "a", "type": "Reader", "params": {"path": str(people)}},
        {"id": "b", "type": "Reader", "params": {"path": str(refs)}},
        {"id": "j", "type": "JoinByField", "params": {"base_keys": ["dep"], "join_keys": ["dep"], **extra}},
    ]
    links = [("a", "output", "j", "base"), ("b", "output", "j", "join")]
    ports = ["j.Joined", "j.UnjoinedBase", "j.UnjoinedJoin"]
    out = run_both(registry, tmp_path, nodes, links, ports)
    for port in ports:
        for route in ROUTES:
            same(out[False][port], out[route][port])
    assert out[("written", "route")] == "sql" and out[("defined", "route")] == "deferred"


def test_joiner_with_keys_of_different_types(registry, tmp_path):
    left, right = tmp_path / "l.parquet", tmp_path / "r.parquet"
    pq.write_table(pa.table({"k": pa.array([1, 2, 3]), "a": pa.array(["x", "y", "z"])}), left)
    pq.write_table(pa.table({"k": pa.array(["2", "3", "4"]), "b": pa.array([20, 30, 40])}), right)
    nodes = [
        {"id": "a", "type": "Reader", "params": {"path": str(left)}},
        {"id": "b", "type": "Reader", "params": {"path": str(right)}},
        {"id": "j", "type": "JoinByField", "params": {"base_keys": ["k"], "join_keys": ["k"]}},
    ]
    out = run_both(
        registry, tmp_path, nodes, [("a", "output", "j", "base"), ("b", "output", "j", "join")], ["j.Joined"]
    )
    for route in ROUTES:
        same(out[False]["j.Joined"], out[route]["j.Joined"])
    assert out["defined"]["j.Joined"].column("b").to_pylist() == [20, 30]


def test_python_route_is_used_when_sql_is_not_possible(registry, people, refs, tmp_path):
    nodes = [
        {"id": "a", "type": "Reader", "params": {"path": str(people)}},
        {"id": "b", "type": "Reader", "params": {"path": str(refs)}},
        {
            "id": "j",
            "type": "JoinByField",
            "params": {"base_keys": ["dep"], "join_keys": ["dep"], "on_multiple": "error"},
        },
    ]
    res = run_flow(
        make_flow(nodes, [("a", "output", "j", "base"), ("b", "output", "j", "join")]),
        registry,
        tmp_path / "run",
        sql_route=True,
    )
    assert res.status == "error" and "plusieurs correspondances" in res.nodes["j"].error  # message de la voie Python


def test_sql_route_failures_are_reported_and_spill_is_cleaned(registry, people, tmp_path):
    nodes = [
        {"id": "r", "type": "Reader", "params": {"path": str(people)}},
        {
            "id": "s",
            "type": "OrderBy",
            "params": {"keys": [{"attribute": "nom", "kind": "number"}]},
        },  # « n3 » n'est pas un nombre
    ]
    res = run_flow(make_flow(nodes, [("r", "output", "s", "input")]), registry, tmp_path / "run", sql_route=True)
    assert res.status == "error" and "SQL (OrderBy)" in res.nodes["s"].error
    assert not (tmp_path / "run" / "s" / ".spill").exists() and not list((tmp_path / "run" / "s").glob("*.tmp"))


def test_small_memory_budget_still_sorts_by_spilling(registry, tmp_path, monkeypatch):
    n = 300_000
    big = tmp_path / "big.parquet"
    rng = np.random.default_rng(3)
    pq.write_table(
        pa.table({"v": pa.array(rng.integers(0, 10**9, n)), "s": pa.array([f"texte {i}" for i in range(n)])}), big
    )
    monkeypatch.setenv("ETL_MEMORY_BUDGET_MB", "400")  # DuckDB reçoit 256 Mo : le tri déborde sur disque si besoin
    nodes = [
        {"id": "r", "type": "Reader", "params": {"path": str(big)}},
        {"id": "s", "type": "OrderBy", "params": {"keys": [{"attribute": "v"}]}},
        {"id": "c", "type": "Counter", "params": {}},  # le tri est lu : il est fait dans le run, et écrit
    ]
    links = [("r", "output", "s", "input"), ("s", "output", "c", "input")]
    res = run_flow(make_flow(nodes, links), registry, tmp_path / "run", sql_route=True)
    assert res.status == "done", res.error
    values = pq.read_table(materialize(tmp_path / "run", "s", "output")).column("v").to_pylist()
    assert values == sorted(values) and len(values) == n


def test_routes_report_counts(registry, people, tmp_path):
    events = []
    nodes = [
        {"id": "r", "type": "Reader", "params": {"path": str(people)}},
        {"id": "s", "type": "OrderBy", "params": {"keys": [{"attribute": "id", "order": "descending"}]}},
    ]
    res = run_flow(
        make_flow(nodes, [("r", "output", "s", "input")]),
        registry,
        tmp_path / "run",
        sql_route=True,
        on_event=events.append,
    )
    assert res.status == "done" and res.nodes["s"].rows == {"output": 50} and res.nodes["s"].rows_in == {"input": 50}
    assert any(e["type"] == "counts" and e["node"] == "s" and e["count"] == 50 for e in events)
    assert any(e.get("message") == "défini par une requête, calculé à la demande" for e in events)  # non lu


def test_unread_port_of_a_blocking_node_is_only_counted_and_defined(registry, people, tmp_path):
    """Statistiques dont seul le résumé est lu (ADR 0044) : le résumé est écrit ; les entités avec les statistiques de
    leur groupe, que rien ne lit, sont comptées et définies, calculées seulement si on les ouvre."""
    events = []
    nodes = [
        {"id": "r", "type": "Reader", "params": {"path": str(people)}},
        {"id": "s", "type": "StatisticsByGroup", "params": {"group_by": ["dep"], "attributes": ["pop"]}},
        {"id": "c", "type": "Counter", "params": {}},
    ]
    links = [("r", "output", "s", "input"), ("s", "summary", "c", "input")]
    run_dir = tmp_path / "run"
    res = run_flow(make_flow(nodes, links), registry, run_dir, sql_route=True, on_event=events.append)
    assert res.status == "done", res.error
    assert res.nodes["s"].rows == {"output": 50, "summary": 4}
    assert (run_dir / "s" / "summary.parquet").is_file()
    assert sql_path(run_dir / "s" / "output.parquet").is_file() and not (run_dir / "s" / "output.parquet").exists()
    assert any(e.get("message") == "calculés à la demande : output" for e in events)
    assert pq.read_table(materialize(run_dir, "s", "output")).num_rows == 50


@pytest.mark.parametrize(
    "kind, params",
    [
        ("BuildList", {"attribute": "nom", "group_by": ["dep"]}),
        ("BuildList", {"attribute": "pop", "group_by": ["dep"], "distinct": True, "sort": True, "geometry": "first"}),
        ("ExecuteSql", {"sql": "SELECT dep, count(*) AS n, sum(pop) AS p FROM input GROUP BY dep ORDER BY dep"}),
        ("ExecuteSql", {"sql": "SELECT id, ST_Buffer(geometry, 1) AS geometry FROM input WHERE pop > 100 ORDER BY id"}),
        ("OrderBy", {"keys": [{"attribute": "nom", "kind": "text"}, {"attribute": "pop", "order": "descending"}]}),
    ],
)
def test_blocking_sql_routes_give_the_batch_result(registry, people, tmp_path, kind, params):
    nodes = [
        {"id": "r", "type": "Reader", "params": {"path": str(people)}},
        {"id": "s", "type": kind, "params": params},
    ]
    out = run_both(registry, tmp_path, nodes, [("r", "output", "s", "input")], ["s.output"])
    for route in ROUTES:
        same(out[False]["s.output"], out[route]["s.output"])
    assert out[("written", "route")] == "sql"
