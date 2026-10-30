"""Lots traités en parallèle : même résultat, même ordre, erreurs et annulation propagées."""

import threading
import time

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest
from etl_engine.executor import run_flow
from etl_engine.sdk import Context, Modifier, Params

from conftest import make_flow


class Jitter(Modifier):
    """Les premiers lots sont les plus lents : sans réordonnancement, la sortie serait mélangée."""

    name = "Jitter"
    label = "Jitter (test)"
    rejected = False
    seen_threads: set[str] = set()

    class Params(Params):
        fail_on: int = -1
        pause: float = 0.0

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        first = batch.column("n")[0].as_py()
        Jitter.seen_threads.add(threading.current_thread().name)
        time.sleep(self.params.pause or (0.05 if first < 10 else 0.0))
        if first == self.params.fail_on:
            raise RuntimeError("lot en échec")
        return {"output": batch.append_column("dbl", pc.multiply(batch.column("n"), 2))}


class Sequential(Jitter):
    name = "Sequential"
    stateful = True


@pytest.fixture
def numbers(tmp_path):
    path = tmp_path / "numbers.parquet"
    pq.write_table(pa.table({"n": pa.array(range(100))}), path)
    return path


def run(registry, numbers, tmp_path, node_type, workers, params=None, **kw):
    registry.register(Jitter)
    registry.register(Sequential)
    flow = make_flow(
        [
            {"id": "r", "type": "Reader", "params": {"path": str(numbers)}},
            {"id": "j", "type": node_type, "params": params or {}},
            {"id": "w", "type": "Writer", "params": {"path": str(tmp_path / "out.parquet")}},
        ],
        [("r", "output", "j", "input"), ("j", "output", "w", "input")],
    )
    return run_flow(flow, registry, tmp_path / "run", batch_rows=10, batch_workers=workers, **kw)


def test_parallel_output_keeps_arrival_order(registry, numbers, tmp_path):
    Jitter.seen_threads.clear()
    res = run(registry, numbers, tmp_path, "Jitter", workers=4)
    assert res.status == "done", res.error
    out = pq.read_table(tmp_path / "out.parquet")
    assert out.column("n").to_pylist() == list(range(100))
    assert out.column("dbl").to_pylist() == [2 * i for i in range(100)]
    assert len(Jitter.seen_threads) > 1  # le travail a réellement été réparti


def test_stateful_modifiers_stay_sequential(registry, numbers, tmp_path):
    Jitter.seen_threads.clear()
    res = run(registry, numbers, tmp_path, "Sequential", workers=4)
    assert res.status == "done", res.error
    assert pq.read_table(tmp_path / "out.parquet").column("n").to_pylist() == list(range(100))
    # un seul thread : celui du nœud (pool des nœuds, ou thread dédié quand il est branché en direct sur son amont)
    assert len(Jitter.seen_threads) == 1 and all(t.startswith(("etl-node", "etl-live")) for t in Jitter.seen_threads)


def test_error_in_a_parallel_batch_fails_the_node(registry, numbers, tmp_path):
    res = run(registry, numbers, tmp_path, "Jitter", workers=4, params={"fail_on": 50})
    assert res.status == "error" and "lot en échec" in res.nodes["j"].error


def test_cancel_stops_parallel_processing(registry, numbers, tmp_path):
    cancel = threading.Event()
    threading.Timer(0.08, cancel.set).start()
    res = run(registry, numbers, tmp_path, "Jitter", workers=2, cancel=cancel, params={"pause": 0.1})
    assert res.status == "cancelled"


def test_single_worker_is_the_sequential_path(registry, numbers, tmp_path):
    Jitter.seen_threads.clear()
    res = run(registry, numbers, tmp_path, "Jitter", workers=1)
    assert res.status == "done"
    assert all(t.startswith(("etl-node", "etl-live")) for t in Jitter.seen_threads)
    assert Context().workers == 1


def test_default_workers_come_from_environment(monkeypatch):
    from etl_engine.duck import batch_workers

    monkeypatch.setenv("ETL_BATCH_WORKERS", "3")
    assert batch_workers(12) == 3
    monkeypatch.delenv("ETL_BATCH_WORKERS")
    assert batch_workers(12) == 6 and batch_workers(2) == 2 and batch_workers(64) == 8
