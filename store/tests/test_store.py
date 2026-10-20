import threading
import time

import pytest
from etl_store import Conflict, NotFound, Store

FLOW = {"name": "f", "nodes": [], "links": []}


@pytest.fixture
def store(tmp_path):
    return Store(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")


def test_flow_crud_and_revision_conflict(store):
    created = store.create_flow("f1", FLOW, "alice")
    assert created["revision"] == 1 and store.get_flow(created["id"])["flow"] == FLOW
    updated = store.update_flow(created["id"], "f1 bis", {**FLOW, "name": "x"}, revision=1)
    assert updated["revision"] == 2
    with pytest.raises(Conflict):
        store.update_flow(created["id"], "f1", FLOW, revision=1)
    assert [f["name"] for f in store.list_flows()] == ["f1 bis"]
    store.delete_flow(created["id"])
    with pytest.raises(NotFound):
        store.get_flow(created["id"])


def test_flow_listing_gives_the_card_fields_without_the_content(store):
    flow = {"name": "Communes", "description": "Prépare les communes", "nodes": [{"id": "a"}, {"id": "b"}], "links": []}
    store.create_flow("Communes", flow, "alice")
    (card,) = store.list_flows()
    assert card["description"] == "Prépare les communes" and card["node_count"] == 2 and "flow" not in card
    store.create_flow("Vide", {"name": "Vide"}, "bob")  # ni description ni nœuds
    vide = next(f for f in store.list_flows() if f["name"] == "Vide")
    assert vide["description"] == "" and vide["node_count"] == 0


def test_claim_is_atomic_and_fifo(store):
    ids = [store.create_run(FLOW, None, {}, {}, "alice")["id"] for _ in range(6)]
    claimed: list[str] = []
    lock = threading.Lock()

    def worker(name):
        while (run := store.claim_next_run(name)) is not None:
            with lock:
                claimed.append(run["id"])

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(claimed) == sorted(ids) and len(claimed) == 6  # chaque run pris une seule fois
    assert all(store.get_run(i, detail=False)["status"] == "running" for i in ids)


def test_concurrency_limits_global_and_per_user(store):
    a = [store.create_run(FLOW, None, {}, {}, "alice")["id"] for _ in range(3)]
    b = store.create_run(FLOW, None, {}, {}, "bob")["id"]
    first = store.claim_next_run("w", max_running=3, max_per_user=2)
    second = store.claim_next_run("w", max_running=3, max_per_user=2)
    assert {first["id"], second["id"]} == {a[0], a[1]}
    third = store.claim_next_run("w", max_running=3, max_per_user=2)  # alice a atteint sa limite : bob passe
    assert third["id"] == b
    assert store.claim_next_run("w", max_running=3, max_per_user=2) is None  # limite globale atteinte


def test_cancel_queued_and_running(store):
    q = store.create_run(FLOW, None, {}, {}, "a")["id"]
    assert store.request_cancel(q)["status"] == "cancelled"
    r = store.create_run(FLOW, None, {}, {}, "a")["id"]
    store.claim_next_run("w")
    store.request_cancel(r)
    assert store.is_cancel_requested(r) and store.get_run(r)["status"] == "running"


def test_events_resume_from_last_id(store):
    rid = store.create_run(FLOW, None, {}, {}, "a")["id"]
    store.add_events(
        rid,
        [
            {"type": "node", "node": "n1", "status": "running"},
            {"type": "counts", "node": "n1", "port": "output", "count": 5},
        ],
    )
    store.add_events(rid, [{"type": "counts", "node": "n1", "port": "output", "count": 9}])
    all_events = store.events_after(rid)
    assert [e["type"] for e in all_events] == ["node", "counts", "counts"]
    after = store.events_after(rid, all_events[0]["id"])
    assert len(after) == 2 and store.last_counts(rid) == {"n1/output": 9}


def test_stale_running_runs_are_interrupted(store):
    rid = store.create_run(FLOW, None, {}, {}, "a")["id"]
    store.claim_next_run("w")
    assert store.interrupt_stale_runs(ttl=60) == []
    time.sleep(0.05)
    assert store.interrupt_stale_runs(ttl=0.01) == [rid]
    assert store.get_run(rid)["status"] == "interrupted"


def test_try_acquire_survives_a_worker_creating_the_lock_just_before(tmp_path, monkeypatch):
    """Deux workers qui démarrent ensemble lisent « pas de verrou » puis l'insèrent : le second ne doit pas planter."""
    from etl_store import Store
    from etl_store.models import MetaRow
    from sqlalchemy.orm import Session

    store = Store(f"sqlite:///{(tmp_path / 'etl.db').as_posix()}")
    assert store.try_acquire("purge", 3600) is True  # le premier worker a créé le verrou

    real_get = Session.get

    def stale_get(self, entity, ident, *args, **kwargs):
        if entity is MetaRow and ident == "lock:purge":
            return None  # le second worker avait lu avant l'insertion du premier
        return real_get(self, entity, ident, *args, **kwargs)

    monkeypatch.setattr(Session, "get", stale_get)
    assert store.try_acquire("purge", 3600) is False  # perdu, sans IntegrityError
