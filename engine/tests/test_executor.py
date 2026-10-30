import threading
import time

import pyarrow.parquet as pq
import pytest
from etl_engine import executor
from etl_engine.analysis import analyze
from etl_engine.cache import has_port
from etl_engine.data import get_crs
from etl_engine.executor import run_flow
from etl_engine.sdk import Modifier, Params

from conftest import make_flow


def basic_flow(src, out_dir, **extra):
    nodes = [
        {"id": "read", "type": "Reader", "params": {"path": str(src)}},
        {"id": "test", "type": "ExtractByExpression", "params": {"conditions": ["\"dep\" = '31'"]}},
        {
            "id": "calc",
            "type": "AddAttributes",
            "params": {"attributes": [{"name": "pop2", "expression": '"pop" * 2'}]},
        },
        {"id": "write", "type": "Writer", "params": {"path": str(out_dir / "out.parquet")}},
        {"id": "rej", "type": "Writer", "params": {"path": str(out_dir / "failed.parquet")}},
    ]
    links = [
        ("read", "output", "test", "input"),
        ("test", "Passed", "calc", "input"),
        ("calc", "output", "write", "input"),
        ("test", "Failed", "rej", "input"),
    ]
    return make_flow(nodes, links, **extra)


def test_end_to_end_counts_and_outputs(registry, communes, tmp_path):
    events = []
    out = tmp_path / "out"
    res = run_flow(basic_flow(communes, out), registry, tmp_path / "run", on_event=events.append)
    assert res.status == "done", res.error
    assert res.nodes["read"].rows == {"output": 20}
    assert res.nodes["test"].rows["Passed"] == 10 and res.nodes["test"].rows["Failed"] == 10
    assert res.nodes["write"].rows_in == {"input": 10}
    got = pq.read_table(out / "out.parquet")
    assert got.num_rows == 10 and "pop2" in got.column_names
    assert pq.read_table(out / "failed.parquet").num_rows == 10
    types = [e["type"] for e in events]
    assert types[0] == "run_started" and types[-1] == "run_finished"
    assert any(e["type"] == "counts" and e["node"] == "read" and e["count"] == 20 for e in events)


def test_cache_off_removes_intermediate_ports(registry, communes, tmp_path):
    run_dir = tmp_path / "run"
    run_flow(basic_flow(communes, tmp_path / "o"), registry, run_dir, cache=False)
    assert not has_port(run_dir / "read" / "output.parquet") and communes.is_file()
    run_flow(basic_flow(communes, tmp_path / "o"), registry, tmp_path / "run2", cache=True)
    assert has_port(tmp_path / "run2" / "read" / "output.parquet")


def test_cache_off_counts_unread_ports_without_writing_them(registry, communes, tmp_path, monkeypatch):
    """Sans cache, un port que rien ne lit sur un nœud dont d'autres ports sont lus n'est pas écrit : compté (« Failed »
    d'un filtre, voie Python), ou défini par sa requête (ports non joints, voie SQL, ADR 0044). Un nœud de la voie
    Python sans consommateur écrit tout : c'est un résultat."""
    stored, routed = {}, {}
    real_sink, real_sql = executor.PortSink, executor.run_sql_route

    def sink(path, declared, on_rows, taps=None, store=True):
        stored[(path.parent.name, path.stem)] = store
        return real_sink(path, declared, on_rows, taps=taps, store=store)

    def sql(inst, port_files, out_paths, *args, **kw):
        routed.update({(inst.ctx.node_id, port): path is not None for port, path in out_paths.items()})
        return real_sql(inst, port_files, out_paths, *args, **kw)

    monkeypatch.setattr(executor, "PortSink", sink)
    monkeypatch.setattr(executor, "run_sql_route", sql)
    nodes = [
        {"id": "read", "type": "Reader", "params": {"path": str(communes)}},
        # random() : non déterministe, le filtre passe par la voie par lots (ses ports ne sont pas définis à la demande)
        {"id": "test", "type": "ExtractByExpression", "params": {"conditions": ['"pop" >= 1000 AND random() >= 0']}},
        {"id": "join", "type": "JoinByField", "params": {"base_keys": ["id"], "join_keys": ["id"]}},
        {"id": "calc", "type": "AddAttributes", "params": {"attributes": [{"name": "k", "expression": "random()"}]}},
    ]
    links = [
        ("read", "output", "test", "input"),
        ("test", "Passed", "join", "base"),
        ("read", "output", "join", "join"),
        ("join", "Joined", "calc", "input"),
    ]
    res = run_flow(make_flow(nodes, links), registry, tmp_path / "run", cache=False)
    assert res.status == "done", res.error
    assert stored[("test", "Passed")] and not stored[("test", "Failed")]
    assert routed[("join", "Joined")] and not routed[("join", "UnjoinedJoin")]
    assert stored[("calc", "output")] and stored[("calc", "Rejected")]  # résultat du run : écrit
    rows = res.nodes["test"].rows
    assert rows["Passed"] + rows["Failed"] == 20 and rows["Failed"] > 0
    assert res.nodes["join"].rows["UnjoinedJoin"] == rows["Failed"]


def test_reuse_unchanged_nodes_and_recompute_downstream(registry, communes, tmp_path):
    out = tmp_path / "o"
    first = tmp_path / "run1"
    r1 = run_flow(basic_flow(communes, out), registry, first)
    assert all(n.status in ("done", "disabled") for n in r1.nodes.values())

    (out / "out.parquet").unlink()
    r2 = run_flow(basic_flow(communes, out), registry, tmp_path / "run2", reuse_from=first)
    writers = {"write", "rej"}  # une écriture est toujours relancée : le fichier est réécrit
    assert {k: v.status for k, v in r2.nodes.items()} == {k: "done" if k in writers else "cached" for k in r2.nodes}
    assert (out / "out.parquet").is_file()

    changed = basic_flow(communes, out)
    changed.node("calc").params["attributes"][0]["expression"] = '"pop" * 3'
    r3 = run_flow(changed, registry, tmp_path / "run3", reuse_from=first)
    status = {k: v.status for k, v in r3.nodes.items()}
    assert status["read"] == "cached" and status["test"] == "cached"
    assert status["calc"] == "done" and status["write"] == "done"
    assert pq.read_table(out / "out.parquet").column("pop2").to_pylist()[1] == 600  # pop de la commune 2 = 200 * 3


def test_target_runs_only_ancestors(registry, communes, tmp_path):
    res = run_flow(basic_flow(communes, tmp_path / "o"), registry, tmp_path / "run", target="test")
    assert res.nodes["test"].status == "done" and res.nodes["read"].status == "done"
    assert res.nodes["calc"].status == "not_run" and res.nodes["write"].status == "not_run"


def test_disabled_node_and_link(registry, communes, tmp_path):
    flow = basic_flow(communes, tmp_path / "o")
    flow.node("rej").enabled = False
    res = run_flow(flow, registry, tmp_path / "run")
    assert res.status == "done" and res.nodes["rej"].status == "disabled"
    flow.links[3].enabled = False
    assert run_flow(flow, registry, tmp_path / "run2").status == "done"


def test_config_error_stops_before_reading_any_data(registry, communes, tmp_path):
    flow = basic_flow(communes, tmp_path / "o")
    flow.node("test").params["conditions"] = []
    events = []
    res = run_flow(flow, registry, tmp_path / "run", on_event=events.append)
    assert res.status == "error" and "condition" in res.error
    assert not any(e["type"] == "node" and e["status"] == "running" for e in events)


def test_unknown_param_and_missing_file_are_reported(registry, tmp_path):
    flow = make_flow([{"id": "r", "type": "Reader", "params": {"chemin": "x"}}], [])
    res = run_flow(flow, registry, tmp_path / "run")
    assert res.status == "error"
    flow = make_flow([{"id": "r", "type": "Reader", "params": {"path": str(tmp_path / "absent.parquet")}}], [])
    res = run_flow(flow, registry, tmp_path / "run2")
    assert res.status == "error" and "introuvable" in res.error


def test_published_params_and_env_expansion(registry, communes, tmp_path, monkeypatch):
    monkeypatch.setenv("ETL_TEST_DEP", "09")
    flow = basic_flow(communes, tmp_path / "o", published_params=[{"name": "DEP", "default": "31"}])
    flow.node("test").params["conditions"] = ["\"dep\" = '${DEP}'"]
    res = run_flow(flow, registry, tmp_path / "r1", params={"DEP": "09"})
    assert res.status == "done" and res.nodes["test"].rows["Passed"] == 10
    flow.node("test").params["conditions"] = ["\"dep\" = '${env:ETL_TEST_DEP}'"]
    assert run_flow(flow, registry, tmp_path / "r2").nodes["test"].rows["Passed"] == 10
    bad = run_flow(flow, registry, tmp_path / "r3", params={"NOPE": "1"})
    assert bad.status == "error" and "inconnus" in bad.error


class Boom(Modifier):
    name = "Boom"
    label = "Boom"

    class Params(Params):
        pass

    def process(self, batch):
        raise RuntimeError("bug du modificateur")


class Slow(Modifier):
    name = "Slow"
    label = "Slow"

    class Params(Params):
        seconds: float = 0.3

    def process(self, batch):
        time.sleep(self.params.seconds)
        return {"output": batch}


@pytest.fixture
def registry_with_test_nodes(registry):
    registry.register(Boom)
    registry.register(Slow)
    return registry


def test_unexpected_error_fails_run_and_names_node(registry_with_test_nodes, communes, tmp_path):
    flow = make_flow(
        [{"id": "r", "type": "Reader", "params": {"path": str(communes)}}, {"id": "b", "type": "Boom", "params": {}}],
        [("r", "output", "b", "input")],
    )
    res = run_flow(flow, registry_with_test_nodes, tmp_path / "run")
    assert res.status == "error" and "b :" in res.error and "RuntimeError" in res.error
    assert res.nodes["b"].status == "error"


def test_independent_branches_run_in_parallel(registry_with_test_nodes, communes, tmp_path):
    nodes = [{"id": "r", "type": "Reader", "params": {"path": str(communes)}}]
    links = []
    for i in range(4):
        nodes.append({"id": f"s{i}", "type": "Slow", "params": {"seconds": 0.6}})
        links.append(("r", "output", f"s{i}", "input"))
    t0 = time.perf_counter()
    res = run_flow(make_flow(nodes, links), registry_with_test_nodes, tmp_path / "run", batch_rows=10, max_workers=4)
    elapsed = time.perf_counter() - t0
    assert res.status == "done"
    # 20 lignes / lots de 10 = 2 lots de 0,6 s par branche : 4,8 s en séquentiel, ~1,2 s en parallèle
    assert elapsed < 3.0, f"branches séquentielles ? {elapsed:.1f} s"


def test_cancel(registry_with_test_nodes, communes, tmp_path):
    cancel = threading.Event()
    flow = make_flow(
        [
            {"id": "r", "type": "Reader", "params": {"path": str(communes)}},
            {"id": "s", "type": "Slow", "params": {"seconds": 0.5}},
        ],
        [("r", "output", "s", "input")],
    )
    threading.Timer(0.3, cancel.set).start()
    res = run_flow(flow, registry_with_test_nodes, tmp_path / "run", cancel=cancel, batch_rows=2)
    assert res.status == "cancelled"


def test_timeout(registry_with_test_nodes, communes, tmp_path):
    flow = make_flow(
        [
            {"id": "r", "type": "Reader", "params": {"path": str(communes)}},
            {"id": "s", "type": "Slow", "params": {"seconds": 0.5}},
        ],
        [("r", "output", "s", "input")],
    )
    res = run_flow(flow, registry_with_test_nodes, tmp_path / "run", timeout=0.4, batch_rows=2)
    assert res.status == "error" and "délai" in res.error


class Clock(Modifier):
    """Source externe (comme une base) : ce qu'elle renvoie change sans que le flux change."""

    name = "TestClock"
    inputs = ()
    reusable = False
    calls = 0

    class Params(Params):
        pass

    def run(self, inputs, emit):
        import pyarrow as pa

        Clock.calls += 1
        emit("output", pa.table({"n": [Clock.calls]}))


def test_external_sources_always_rerun_and_refresh_their_downstream(registry, tmp_path):
    registry.register(Clock)
    nodes = [
        {"id": "src", "type": "TestClock", "params": {}},
        {"id": "keep", "type": "ExtractByExpression", "params": {"conditions": ['"n" > 0']}},
    ]
    flow = make_flow(nodes, [("src", "output", "keep", "input")])
    first = run_flow(flow, registry, tmp_path / "run1")
    again = run_flow(flow, registry, tmp_path / "run2", reuse_from=tmp_path / "run1")
    assert first.status == again.status == "done"
    assert again.nodes["src"].status == "done" and again.nodes["keep"].status == "done"  # rien de périmé repris
    assert pq.read_table(tmp_path / "run2" / "keep" / "Passed.parquet").column("n").to_pylist() == [2]


class Rebuild(Modifier):
    """Plugin qui reconstruit sa table colonne par colonne : sans les métadonnées du schéma, donc sans le SCR."""

    name = "TestRebuild"

    class Params(Params):
        pass

    def output_schema(self, inputs):
        import pyarrow as pa

        schema = inputs.get("input")
        return {"output": pa.schema(list(schema)) if schema is not None else None}

    def process(self, batch):
        import pyarrow as pa

        return {"output": pa.Table.from_arrays(batch.columns, names=batch.column_names)}


def test_a_geometry_rebuilt_without_its_metadata_keeps_the_crs_of_its_input(registry, communes, tmp_path):
    """Sans SCR, la carte de l'inspecteur ne s'affiche pas et un export n'a plus de projection : la sortie reprend
    celui de son entrée, à l'exécution comme dans le schéma déclaré (contrôles avant le run, voie SQL)."""
    registry.register(Rebuild)
    nodes = [{"id": "r", "type": "Reader", "params": {"path": str(communes)}}, {"id": "p", "type": "TestRebuild"}]
    flow = make_flow(nodes, [("r", "output", "p", "input")])
    res = run_flow(flow, registry, tmp_path / "run")
    assert res.status == "done", res.error
    assert get_crs(pq.read_schema(tmp_path / "run" / "p" / "output.parquet")) == "EPSG:2154"
    assert analyze(flow, registry)["schemas"]["p/output"]["crs"] == "EPSG:2154"


class Database(Clock):
    """Lecture d'une base : relue à chaque run, reprise dans un run « jusqu'au nœud »."""

    name = "TestDatabase"
    reusable = "partial"


def test_run_to_a_node_reuses_the_cache_even_of_a_database(registry, tmp_path):
    registry.register(Database)
    Clock.calls = 0
    nodes = [
        {"id": "db", "type": "TestDatabase", "params": {}},
        {"id": "a", "type": "ExtractByExpression", "params": {"conditions": ['"n" > 0']}},
        {"id": "b", "type": "ExtractByExpression", "params": {"conditions": ['"n" > 1']}},
    ]
    flow = make_flow(nodes, [("db", "output", "a", "input"), ("db", "output", "b", "input")])
    full = run_flow(flow, registry, tmp_path / "run1")
    assert full.status == "done" and Clock.calls == 1

    # jusqu'à « a » : la base et « a » viennent du cache ; « b », hors de la branche, passe aussi dans ce run
    to_a = run_flow(flow, registry, tmp_path / "run2", reuse_from=tmp_path / "run1", target="a")
    assert {k: v.status for k, v in to_a.nodes.items()} == {"db": "cached", "a": "cached", "b": "cached"}
    assert Clock.calls == 1  # la base n'a pas été relue
    assert to_a.nodes["b"].rows == full.nodes["b"].rows

    # jusqu'à « b » depuis ce run partiel : toujours rien à recalculer
    to_b = run_flow(flow, registry, tmp_path / "run3", reuse_from=tmp_path / "run2", target="b")
    assert to_b.nodes["b"].status == "cached" and Clock.calls == 1

    # un run complet relit la base, et recalcule son aval
    again = run_flow(flow, registry, tmp_path / "run4", reuse_from=tmp_path / "run3")
    assert again.nodes["db"].status == "done" and again.nodes["a"].status == "done" and Clock.calls == 2

    # un réglage changé dans la branche visée : seul ce nœud tourne, la base reste en cache
    changed = make_flow(
        [*nodes[:1], {**nodes[1], "params": {"conditions": ['"n" > 1']}}, nodes[2]],
        [("db", "output", "a", "input"), ("db", "output", "b", "input")],
    )
    edited = run_flow(changed, registry, tmp_path / "run5", reuse_from=tmp_path / "run4", target="a")
    assert edited.nodes["db"].status == "cached" and edited.nodes["a"].status == "done" and Clock.calls == 2


def test_run_to_a_node_keeps_the_results_of_the_other_branches(registry, communes, tmp_path):
    """« Jusqu'au nœud » sur une branche : les autres gardent leurs derniers résultats, même une source toujours
    relancée et son aval ; annoncés après `run_started` (sinon l'interface les efface)."""
    registry.register(Clock)
    Clock.calls = 0
    nodes = [
        {"id": "clock", "type": "TestClock", "params": {}},
        {"id": "keep", "type": "ExtractByExpression", "params": {"conditions": ['"n" > 0']}},
        {"id": "read", "type": "Reader", "params": {"path": str(communes)}},
        {"id": "test", "type": "ExtractByExpression", "params": {"conditions": ["\"dep\" = '31'"]}},
    ]
    flow = make_flow(nodes, [("clock", "output", "keep", "input"), ("read", "output", "test", "input")])
    full = run_flow(flow, registry, tmp_path / "run1")
    assert full.status == "done" and Clock.calls == 1

    events = []
    to_test = run_flow(
        flow, registry, tmp_path / "run2", reuse_from=tmp_path / "run1", target="test", on_event=events.append
    )
    assert {k: v.status for k, v in to_test.nodes.items()} == {
        "clock": "cached",
        "keep": "cached",
        "read": "cached",
        "test": "cached",
    }
    assert Clock.calls == 1 and to_test.nodes["keep"].rows == full.nodes["keep"].rows
    assert pq.read_table(tmp_path / "run2" / "keep" / "Passed.parquet").column("n").to_pylist() == [1]
    kinds = [e["type"] for e in events]
    assert kinds.index("run_started") < min(i for i, e in enumerate(events) if e["type"] == "node")

    # un run complet relance toujours la source et son aval
    again = run_flow(flow, registry, tmp_path / "run3", reuse_from=tmp_path / "run2")
    assert again.nodes["clock"].status == again.nodes["keep"].status == "done" and Clock.calls == 2
    assert again.nodes["test"].status == "cached"

    # un réglage changé hors de la branche visée : ce nœud n'est pas gardé (ses données seraient périmées)
    edited = make_flow(
        [*nodes[:1], {**nodes[1], "params": {"conditions": ['"n" > 5']}}, *nodes[2:]],
        [("clock", "output", "keep", "input"), ("read", "output", "test", "input")],
    )
    partial = run_flow(edited, registry, tmp_path / "run4", reuse_from=tmp_path / "run3", target="test")
    assert partial.nodes["clock"].status == "cached" and partial.nodes["keep"].status == "not_run"


def test_reused_nodes_are_available_before_a_slow_node_ends(registry_with_test_nodes, communes, tmp_path):
    """Un nœud repris passe dès le début du run, sans attendre que le seul thread libre ait fini un nœud long."""
    nodes = [
        {"id": "r", "type": "Reader", "params": {"path": str(communes)}},
        {"id": "s", "type": "Slow", "params": {"seconds": 0.0}},
        {"id": "x", "type": "ExtractByExpression", "params": {"conditions": ["\"dep\" = '31'"]}},
    ]
    links = [("r", "output", "s", "input"), ("r", "output", "x", "input")]
    run_flow(make_flow(nodes, links), registry_with_test_nodes, tmp_path / "run1")
    slower = [nodes[0], {**nodes[1], "params": {"seconds": 0.5}}, nodes[2]]
    events = []
    res = run_flow(
        make_flow(slower, links),
        registry_with_test_nodes,
        tmp_path / "run2",
        reuse_from=tmp_path / "run1",
        max_workers=1,
        on_event=events.append,
    )
    assert res.status == "done" and res.nodes["x"].status == "cached" and res.nodes["s"].status == "done"
    order = [(e["node"], e["status"]) for e in events if e["type"] == "node"]
    assert order.index(("x", "cached")) < order.index(("s", "running"))
