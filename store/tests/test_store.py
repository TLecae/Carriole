import socket
import threading
import time

import pytest
from etl_store import Conflict, NotFound, Store

FLOW = {"name": "f", "nodes": [], "links": []}


@pytest.fixture
def store(store_url):
    return Store(store_url)


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
    second = store.claim_next_run("w", max_running=3, max_per_user=2)  # file équitable : bob n'a rien en cours
    third = store.claim_next_run("w", max_running=3, max_per_user=2)
    assert [first["id"], second["id"], third["id"]] == [a[0], b, a[1]]
    assert store.claim_next_run("w", max_running=3, max_per_user=2) is None  # limite globale atteinte


def test_fair_queue_lets_others_pass_a_long_batch(store):
    batch = [store.create_run(FLOW, None, {}, {}, "alice")["id"] for _ in range(5)]
    late = store.create_run(FLOW, None, {}, {}, "bob")["id"]
    assert store.claim_next_run("w")["id"] == batch[0]
    assert store.claim_next_run("w")["id"] == late  # alice a déjà un run en cours, bob aucun
    assert store.claim_next_run("w")["id"] == batch[1]


def test_cancel_queued_and_running(store):
    q = store.create_run(FLOW, None, {}, {}, "a")["id"]
    assert store.request_cancel(q)["status"] == "cancelled"
    r = store.create_run(FLOW, None, {}, {}, "a")["id"]
    store.claim_next_run("w")
    store.request_cancel(r)
    assert store.is_cancel_requested(r) and store.get_run(r)["status"] == "running"


def test_pause_and_resume_a_running_run(store):
    """Pause : seulement un run qui tourne ; il reste « running » (place, concurrence, cache protégé) ; la fin du run
    (ou son interruption) lève la pause."""
    import pytest

    q = store.create_run(FLOW, None, {}, {}, "a")["id"]
    with pytest.raises(ValueError):
        store.request_pause(q, True)  # en attente : rien à mettre en pause
    store.claim_next_run("w")
    assert store.request_pause(q, True)["paused"] is True
    assert store.run_control(q) == (False, True) and store.get_run(q)["status"] == "running"
    assert store.count_running() == 1 and q in store.protected_runs()
    assert store.request_pause(q, False)["paused"] is False and store.run_control(q) == (False, False)
    store.request_pause(q, True)
    store.request_cancel(q)
    assert store.run_control(q) == (True, True)
    with pytest.raises(ValueError):
        store.request_pause(q, False)  # arrêt demandé : la reprise n'a plus de sens
    store.finish_run(q, "cancelled", None, None)
    assert store.get_run(q)["paused"] is False
    with pytest.raises(ValueError):
        store.request_pause(q, True)


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


def test_a_run_whose_worker_still_runs_here_is_not_interrupted(store):
    """Un calcul qui garde le GIL (shapely.polygonize) bloque le heartbeat ; le worker, vivant, n'a pas abandonné son
    run. Celui d'un worker disparu est bien interrompu."""
    from etl_store.workers import worker_name

    busy = store.create_run(FLOW, None, {}, {}, "a")["id"]
    store.claim_next_run(worker_name())  # ce processus : vivant, sur ce poste
    gone = store.create_run(FLOW, None, {}, {}, "a")["id"]
    store.claim_next_run(f"{socket.gethostname()}-{2**31 - 1}")  # aucun processus de ce numéro
    time.sleep(0.05)
    assert store.interrupt_stale_runs(ttl=0.01) == [gone]
    assert store.get_run(busy)["status"] == "running"


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


def test_database_address_can_come_from_a_secret_file(tmp_path, monkeypatch):
    from etl_store import Settings

    secret = tmp_path / "db_url"
    secret.write_bytes(f"sqlite:///{(tmp_path / 'secret.db').as_posix()}\n".encode())
    monkeypatch.delenv("ETL_DB_URL", raising=False)
    monkeypatch.setenv("ETL_DB_URL_FILE", str(secret))
    assert Settings(meta_dir=tmp_path / "meta").url.endswith("/secret.db")
    monkeypatch.setenv("ETL_DB_URL", "sqlite:///autre.db")  # la variable l'emporte sur le fichier
    assert Settings(meta_dir=tmp_path / "meta").url == "sqlite:///autre.db"
