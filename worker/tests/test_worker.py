import json
import threading
import time

import pytest
from etl_engine.flow import Flow
from etl_engine.registry import Registry
from etl_engine.sdk import Modifier, Params
from etl_store import Settings, Store
from etl_worker.main import run, serve
from etl_worker.runner import execute_run


def test_heartbeat_file_is_written_and_loop_stops(tmp_path):
    hb = tmp_path / "hb"
    stop = threading.Event()
    beats = []
    t = threading.Thread(target=run, args=(stop, hb, 0.01, lambda: beats.append(1)))
    t.start()
    while not hb.exists():
        pass
    stop.set()
    t.join(timeout=2)
    assert not t.is_alive() and float(hb.read_text()) > 0 and beats


class SlowNode(Modifier):
    name = "SlowNode"
    label = "SlowNode"

    class Params(Params):
        seconds: float = 0.4

    def process(self, batch):
        time.sleep(self.params.seconds)
        return {"output": batch}


@pytest.fixture
def env(tmp_path):
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    src = tmp_path / "in.parquet"
    pq.write_table(pa.table({"id": np.arange(30), "dep": ["31"] * 30}), src)
    settings = Settings(meta_dir=tmp_path / "meta", cache_dir=tmp_path / "cache", stale_seconds=30)
    store = Store(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    registry = Registry.discover()
    registry.register(SlowNode)
    return store, registry, settings, src, tmp_path


def flow_json(src, out, slow=0.0):
    nodes = [{"id": "r", "type": "Reader", "params": {"path": str(src)}}]
    links = []
    last = "r"
    if slow:
        nodes.append({"id": "s", "type": "SlowNode", "params": {"seconds": slow}})
        links.append({"id": "l0", "from": ["r", "output"], "to": ["s", "input"]})
        last = "s"
    nodes.append({"id": "w", "type": "Writer", "params": {"path": str(out)}})
    links.append({"id": "l1", "from": [last, "output"], "to": ["w", "input"]})
    return Flow.model_validate({"name": "t", "nodes": nodes, "links": links}).model_dump(mode="json", by_alias=True)


def test_execute_run_writes_status_result_and_events(env):
    store, registry, settings, src, tmp = env
    rid = store.create_run(flow_json(src, tmp / "out.parquet"), None, {}, {"cache": True}, "alice")["id"]
    run_row = store.claim_next_run("w1")
    assert execute_run(store, registry, run_row, settings, threading.Event()) == "done"
    final = store.get_run(rid)
    assert (
        final["status"] == "done" and final["finished_at"] and final["result"]["nodes"]["r"]["rows"] == {"output": 30}
    )
    kinds = [e["type"] for e in store.events_after(rid)]
    assert kinds[0] == "run_started" and kinds[-1] == "run_finished" and "counts" in kinds
    assert (settings.cache_dir / rid / "r" / "output.parquet").exists()
    assert (tmp / "out.parquet").exists()


def test_user_cancel_marks_cancelled(env):
    store, registry, settings, src, tmp = env
    flow = flow_json(src, tmp / "o.parquet", slow=1.6)  # le surveillant lit le drapeau toutes les secondes
    rid = store.create_run(flow, None, {}, {}, "a")["id"]
    run_row = store.claim_next_run("w")
    threading.Timer(0.3, store.request_cancel, args=(rid,)).start()
    t0 = time.time()
    status = execute_run(store, registry, run_row, settings, threading.Event())
    assert status == "cancelled" and time.time() - t0 < 6
    assert store.get_run(rid)["status"] == "cancelled"


def test_worker_shutdown_marks_run_interrupted(env):
    store, registry, settings, src, tmp = env
    rid = store.create_run(flow_json(src, tmp / "o.parquet", slow=1.6), None, {}, {}, "a")["id"]
    run_row = store.claim_next_run("w")
    shutdown = threading.Event()
    threading.Timer(0.3, shutdown.set).start()
    assert execute_run(store, registry, run_row, settings, shutdown) == "interrupted"
    assert store.get_run(rid)["status"] == "interrupted"


def test_invalid_flow_run_ends_in_error_and_worker_survives(env):
    store, registry, settings, src, tmp = env
    bad = flow_json(src, tmp / "o.parquet")
    bad["nodes"][0]["type"] = "Inconnu"
    rid = store.create_run(bad, None, {}, {}, "a")["id"]
    run_row = store.claim_next_run("w")
    assert execute_run(store, registry, run_row, settings, threading.Event()) == "error"
    assert "inconnu" in store.get_run(rid)["error"]


def test_serve_loop_processes_queue_then_stops(env, monkeypatch):
    store, registry, settings, src, tmp = env
    monkeypatch.setenv("ETL_HEARTBEAT_FILE", str(tmp / "hb"))
    ids = [store.create_run(flow_json(src, tmp / f"o{i}.parquet"), None, {}, {}, "a")["id"] for i in range(2)]
    shutdown = threading.Event()
    t = threading.Thread(target=serve, args=(store, registry, settings, shutdown, "w"))
    t.start()
    deadline = time.time() + 20
    while time.time() < deadline and any(store.get_run(i, False)["status"] != "done" for i in ids):
        time.sleep(0.1)
    shutdown.set()
    t.join(timeout=5)
    assert not t.is_alive() and [store.get_run(i, False)["status"] for i in ids] == ["done", "done"]
    assert json.loads(json.dumps(store.get_run(ids[0])["result"]))["status"] == "done"


# --------------------------------------------------------------------------------- notification et purge


class FakeSMTP:
    sent: list = []
    fail = False

    def __init__(self, host, port, timeout=0):
        if FakeSMTP.fail:
            raise OSError("connexion refusée")
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        self.logged = (user, password)

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.sent, FakeSMTP.fail = [], False
    monkeypatch.setattr("etl_worker.notify.smtplib.SMTP", FakeSMTP)
    return FakeSMTP


def run_for_mail(**options):
    return {"id": "r1", "flow_id": "f1", "flow_name": "Mon flux", "options": options}


def test_mail_is_sent_with_status_errors_and_link(smtp):
    from etl_worker.notify import notify_run_finished

    settings = Settings(smtp_host="smtp.test", smtp_from="etl@test", public_url="https://etl.test")
    result = {
        "seconds": 12.3,
        "nodes": {
            "w": {"status": "error", "error": "disque plein", "rows": {}},
            "t": {"status": "done", "rows": {"Rejected": 4}},
        },
    }
    assert notify_run_finished(settings, run_for_mail(notify_email="moi@test.org"), "error", "w : disque plein", result)
    (msg,) = smtp.sent
    assert msg["To"] == "moi@test.org" and "Mon flux" in msg["Subject"] and "en échec" in msg["Subject"]
    body = msg.get_content()
    assert "12.3 s" in body and "w (disque plein)" in body and "4 entité(s) rejetée(s)" in body
    assert "https://etl.test/#flow=f1" in body


def test_no_mail_without_request_or_smtp_and_failures_do_not_raise(smtp):
    from etl_worker.notify import notify_run_finished

    configured = Settings(smtp_host="smtp.test")
    assert notify_run_finished(configured, run_for_mail(), "done", None, None) is False  # pas demandé
    assert (
        notify_run_finished(Settings(), run_for_mail(notify_email="a@b.fr"), "done", None, None) is False
    )  # pas de SMTP
    smtp.fail = True
    assert notify_run_finished(configured, run_for_mail(notify_email="a@b.fr"), "done", None, None) is False
    assert smtp.sent == []


def test_execute_run_sends_the_requested_mail(env, smtp):
    store, registry, settings, src, tmp = env
    settings = Settings(meta_dir=settings.meta_dir, cache_dir=settings.cache_dir, smtp_host="smtp.test")
    rid = store.create_run(flow_json(src, tmp / "m.parquet"), None, {}, {"notify_email": "moi@test.org"}, "a")["id"]
    assert execute_run(store, registry, store.claim_next_run("w"), settings, threading.Event()) == "done"
    assert len(smtp.sent) == 1 and smtp.sent[0]["To"] == "moi@test.org"
    assert store.get_run(rid)["status"] == "done"


def test_worker_purges_old_caches_between_runs(env, monkeypatch):
    from sqlalchemy import text

    store, registry, settings, src, tmp = env
    monkeypatch.setenv("ETL_HEARTBEAT_FILE", str(tmp / "hb"))
    old = store.create_run(flow_json(src, tmp / "o.parquet"), None, {}, {}, "a")["id"]
    with store.engine.begin() as con:
        con.execute(
            text("UPDATE runs SET status='done', finished_at=:t WHERE id=:i"), {"t": time.time() - 40 * 86400, "i": old}
        )
    (settings.cache_dir / old / "n").mkdir(parents=True)
    (settings.cache_dir / old / "n" / "output.parquet").write_bytes(b"x" * 100)
    purging = Settings(
        meta_dir=settings.meta_dir, cache_dir=settings.cache_dir, cache_retention_days=14, purge_interval=0
    )
    shutdown = threading.Event()
    t = threading.Thread(target=serve, args=(store, registry, purging, shutdown, "w"))
    t.start()
    deadline = time.time() + 10
    while time.time() < deadline and (settings.cache_dir / old).exists():
        time.sleep(0.1)
    shutdown.set()
    t.join(timeout=5)
    assert not (settings.cache_dir / old).exists() and store.get_run(old)["purged_at"] is not None


def test_desktop_limits_allow_as_many_runs_as_workers_for_a_single_user(env):
    """Poste de bureau : un seul utilisateur, plafond = nombre de workers (pas 2 par utilisateur)."""
    store, *_ = env
    for _ in range(5):
        store.create_run({}, None, {}, {}, "alice")
    claimed = [store.claim_next_run("w", max_running=4, max_per_user=0) for _ in range(5)]
    assert [c is not None for c in claimed] == [True, True, True, True, False]


def test_each_run_gets_its_share_of_the_machine(env, monkeypatch):
    from etl_engine import duck, executor

    store, registry, settings, src, tmp = env
    monkeypatch.setenv("ETL_MEMORY_BUDGET_MB", "6000")
    monkeypatch.setattr(duck, "machine_threads", lambda: 12)
    seen = []
    real = executor.run_flow

    def spy(*args, **kwargs):
        seen.append((kwargs["memory_mb"], kwargs["threads"]))
        return real(*args, **kwargs)

    monkeypatch.setattr("etl_worker.runner.run_flow", spy)
    for name in ("a", "b"):
        store.create_run(flow_json(src, tmp / f"{name}.parquet"), None, {}, {}, "alice")
    first = store.claim_next_run("w1", max_running=4)
    assert execute_run(store, registry, first, settings, threading.Event()) == "done"
    assert seen == [(6000, 12)]  # seul : toute la machine
    second, third = store.claim_next_run("w1", max_running=4), None
    store.create_run(flow_json(src, tmp / "c.parquet"), None, {}, {}, "alice")
    third = store.claim_next_run("w2", max_running=4)  # deux runs en cours : second et third
    assert execute_run(store, registry, third, settings, threading.Event()) == "done"
    assert seen[-1] == (3000, 6)
    assert second is not None
