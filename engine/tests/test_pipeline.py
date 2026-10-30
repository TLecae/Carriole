"""Nœuds branchés en direct : un nœud à une entrée travaille en même temps que son amont (ADR 0013)."""

import threading
import time

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from etl_engine import executor
from etl_engine.blocking import collect
from etl_engine.executor import run_flow
from etl_engine.sdk import Modifier, Params

from conftest import make_flow

LOG: list[tuple[str, int, float, float]] = []  # (nœud, premier n du lot, début, fin)
_log_lock = threading.Lock()


class Stamp(Modifier):
    """Laisse passer les lots en notant quand il les traite ; peut échouer ou manquer de mémoire sur un lot."""

    name = "Stamp"
    rejected = False
    stateful = True  # un lot à la fois : les horodatages suivent l'ordre

    class Params(Params):
        pause: float = 0.01
        fail_on: int = -1
        memory_on: int = -1

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        first = batch.column("n")[0].as_py()
        t0 = time.perf_counter()
        time.sleep(self.params.pause)
        if first == self.params.fail_on:
            raise RuntimeError("boom")
        if first == self.params.memory_on:
            raise MemoryError
        with _log_lock:
            LOG.append((self.ctx.node_id, first, t0, time.perf_counter()))
        return {"output": batch}


class TakeFirst(Modifier):
    """Ne lit que le premier lot puis s'arrête : l'amont ne doit pas rester bloqué."""

    name = "TakeFirst"
    rejected = False

    def run(self, inputs, emit):
        for batch in inputs.get("input", ()):
            emit("output", batch)
            return


class Gather(Modifier):
    """Bloquant : lit tout, puis émet."""

    name = "Gather"
    rejected = False
    mode = "blocking"
    spans: list[tuple[str, float, float]] = []

    def run(self, inputs, emit):
        t0 = time.perf_counter()
        table = collect(inputs.get("input", ()))
        time.sleep(0.2)
        Gather.spans.append((self.ctx.node_id, t0, time.perf_counter()))
        if table is not None:
            emit("output", table)


@pytest.fixture
def numbers(tmp_path, registry):
    for cls in (Stamp, TakeFirst, Gather):
        registry.register(cls)
    LOG.clear()
    Gather.spans.clear()
    path = tmp_path / "numbers.parquet"
    pq.write_table(pa.table({"n": pa.array(range(100))}), path, row_group_size=10)
    return path


def chain(numbers, out, *middle):
    """Reader → nœuds du milieu → Writer."""
    nodes = [{"id": "r", "type": "Reader", "params": {"path": str(numbers)}}]
    nodes += [{"id": nid, "type": kind, "params": params} for nid, kind, params in middle]
    nodes.append({"id": "w", "type": "Writer", "params": {"path": str(out)}})
    ids = [n["id"] for n in nodes]
    return make_flow(nodes, [(a, "output", b, "input") for a, b in zip(ids, ids[1:], strict=False)])


def first_and_last(node):
    times = sorted((t0, t1) for n, _, t0, t1 in LOG if n == node)
    return times[0][0], times[-1][1]


def test_chain_runs_concurrently_and_keeps_order(registry, numbers, tmp_path):
    out = tmp_path / "out.parquet"
    flow = chain(numbers, out, ("a", "Stamp", {}), ("b", "Stamp", {}))
    res = run_flow(flow, registry, tmp_path / "run", batch_rows=10, timeout=60)
    assert res.status == "done", res.error
    assert pq.read_table(out).column("n").to_pylist() == list(range(100))
    assert res.nodes["b"].rows_in == {"input": 100} and res.nodes["w"].rows_in == {"input": 100}
    b_start, _ = first_and_last("b")
    _, a_end = first_and_last("a")
    assert b_start < a_end  # b a commencé avant que a ait fini
    assert (tmp_path / "run" / "a" / "output.parquet").exists()  # le port reste écrit (cache, inspecteur)


def test_without_pipeline_nodes_wait_for_their_upstream(registry, numbers, tmp_path):
    flow = chain(numbers, tmp_path / "out.parquet", ("a", "Stamp", {}), ("b", "Stamp", {}))
    res = run_flow(flow, registry, tmp_path / "run", batch_rows=10, pipeline=False)
    assert res.status == "done"
    assert first_and_last("b")[0] > first_and_last("a")[1]


def test_fork_feeds_two_live_consumers(registry, numbers, tmp_path):
    nodes = [
        {"id": "r", "type": "Reader", "params": {"path": str(numbers)}},
        {"id": "a", "type": "Stamp", "params": {}},
        {"id": "b", "type": "Stamp", "params": {"pause": 0.03}},
        {"id": "wa", "type": "Writer", "params": {"path": str(tmp_path / "a.parquet")}},
        {"id": "wb", "type": "Writer", "params": {"path": str(tmp_path / "b.parquet")}},
    ]
    links = [("r", "output", "a", "input"), ("r", "output", "b", "input")]
    links += [("a", "output", "wa", "input"), ("b", "output", "wb", "input")]
    res = run_flow(make_flow(nodes, links), registry, tmp_path / "run", batch_rows=10, cache=False, timeout=60)
    assert res.status == "done", res.error
    for name in ("a", "b"):
        assert pq.read_table(tmp_path / f"{name}.parquet").column("n").to_pylist() == list(range(100))


def test_consumer_that_stops_early_does_not_block_its_upstream(registry, numbers, tmp_path):
    out = tmp_path / "out.parquet"
    res = run_flow(chain(numbers, out, ("f", "TakeFirst", {})), registry, tmp_path / "run", batch_rows=10, timeout=30)
    assert res.status == "done", res.error
    assert pq.read_table(out).num_rows == 10 and res.nodes["r"].rows == {"output": 100}


def test_upstream_error_cancels_live_consumers(registry, numbers, tmp_path):
    flow = chain(numbers, tmp_path / "out.parquet", ("a", "Stamp", {"fail_on": 50}), ("b", "Stamp", {}))
    res = run_flow(flow, registry, tmp_path / "run", batch_rows=10, timeout=30)
    assert res.status == "error" and "boom" in res.error and res.error.startswith("a")
    assert res.nodes["b"].status == "cancelled" and not (tmp_path / "out.parquet").exists()


def test_downstream_error_stops_the_run(registry, numbers, tmp_path):
    flow = chain(numbers, tmp_path / "out.parquet", ("a", "Stamp", {}), ("b", "Stamp", {"fail_on": 30}))
    res = run_flow(flow, registry, tmp_path / "run", batch_rows=10, timeout=30)
    assert res.status == "error" and res.error.startswith("b") and "boom" in res.error


def test_cancel_stops_a_live_chain(registry, numbers, tmp_path):
    cancel = threading.Event()
    threading.Timer(0.15, cancel.set).start()
    flow = chain(numbers, tmp_path / "out.parquet", ("a", "Stamp", {"pause": 0.05}), ("b", "Stamp", {}))
    t0 = time.perf_counter()
    res = run_flow(flow, registry, tmp_path / "run", batch_rows=10, cancel=cancel, timeout=30)
    assert res.status == "cancelled" and time.perf_counter() - t0 < 5


def test_run_without_cache_streams_without_intermediate_files(registry, numbers, tmp_path, monkeypatch):
    stored = {}
    real = executor.PortSink

    def spy(path, declared, on_rows, taps=None, store=True):
        stored[(path.parent.name, path.stem)] = store
        return real(path, declared, on_rows, taps=taps, store=store)

    monkeypatch.setattr(executor, "PortSink", spy)
    out = tmp_path / "out.parquet"
    flow = chain(numbers, out, ("a", "Stamp", {}))
    # voie Python : le lecteur passe ses lots en direct (par DuckDB, il écrirait son port d'un coup)
    res = run_flow(flow, registry, tmp_path / "run", batch_rows=10, cache=False, sql_route=False)
    assert res.status == "done" and pq.read_table(out).num_rows == 100
    assert stored[("r", "output")] is False and stored[("a", "output")] is False
    run_flow(chain(numbers, out, ("a", "Stamp", {})), registry, tmp_path / "run2", batch_rows=10)
    assert stored[("a", "output")] is True  # avec cache : le port est écrit


def test_reused_upstream_is_read_from_its_file(registry, numbers, tmp_path):
    out = tmp_path / "out.parquet"
    first = tmp_path / "run1"
    assert run_flow(chain(numbers, out, ("a", "Stamp", {})), registry, first, batch_rows=10).status == "done"
    res = run_flow(chain(numbers, out, ("a", "Stamp", {"pause": 0.0})), registry, tmp_path / "run2", reuse_from=first)
    assert res.status == "done" and res.nodes["r"].status == "cached"
    assert pq.read_table(out).column("n").to_pylist() == list(range(100))


def test_memory_error_gives_a_clear_message(registry, numbers, tmp_path):
    flow = chain(numbers, tmp_path / "out.parquet", ("a", "Stamp", {"memory_on": 20}))
    res = run_flow(flow, registry, tmp_path / "run", batch_rows=10, timeout=30)
    assert res.status == "error" and "mémoire insuffisante" in res.nodes["a"].error
    res = run_flow(flow, registry, tmp_path / "run_en", batch_rows=10, timeout=30, lang="en")
    assert "not enough memory" in res.error


def test_memory_guard_stops_the_run_when_the_machine_is_full(registry, numbers, tmp_path, monkeypatch):
    class Full:
        total, available = 16 * 2**30, 10 * 2**20

    monkeypatch.setattr(executor.psutil, "virtual_memory", lambda: Full)
    monkeypatch.setattr(executor._RssSampler, "_sample", lambda self: 9000.0)
    flow = chain(numbers, tmp_path / "out.parquet", ("a", "Stamp", {"pause": 0.05}))
    res = run_flow(flow, registry, tmp_path / "run", batch_rows=10, memory_mb=1024, timeout=30)
    assert res.status == "error" and "mémoire insuffisante pendant" in res.error and "8.8 Go" in res.error


def test_blocking_nodes_take_turns(registry, numbers, tmp_path, monkeypatch):
    monkeypatch.setenv("ETL_BLOCKING_NODES", "1")
    nodes = [
        {"id": "r", "type": "Reader", "params": {"path": str(numbers)}},
        {"id": "g1", "type": "Gather", "params": {}},
        {"id": "g2", "type": "Gather", "params": {}},
    ]
    links = [("r", "output", "g1", "input"), ("r", "output", "g2", "input")]
    res = run_flow(make_flow(nodes, links), registry, tmp_path / "run", pipeline=False)
    assert res.status == "done"
    (_, s1, e1), (_, s2, e2) = sorted(Gather.spans, key=lambda s: s[1])
    assert e1 <= s2  # l'un après l'autre
