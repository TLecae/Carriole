import threading
import time

import pytest
from etl_store import Store
from etl_worker import scheduler

FLOW = {
    "name": "Flux planifié",
    "published_params": [{"name": "DEP", "default": "31", "required": True}],
    "nodes": [{"id": "r", "type": "Reader", "params": {"path": "/data/in/x.csv"}}],
    "links": [],
}


@pytest.fixture
def store(tmp_path):
    return Store(f"sqlite:///{(tmp_path / 'etl.db').as_posix()}")


def make_schedule(store, flow_id, cron="* * * * *", due_in=-5.0, **extra):
    data = {"name": "nuit", "flow_id": flow_id, "cron": cron, "params": {"DEP": "09"}, "options": {"cache": True}}
    data.update(extra)
    return store.create_schedule(data, "alice", time.time() + due_in)


def test_due_schedule_creates_a_queued_run_and_moves_to_next_occurrence(store):
    flow = store.create_flow("f", FLOW, "alice")
    sched = make_schedule(store, flow["id"], cron="0 3 * * *")
    now = time.time()
    created = scheduler.tick(store, now)
    assert len(created) == 1
    run = store.get_run(created[0])
    assert run["status"] == "queued" and run["owner"] == "alice" and run["flow_id"] == flow["id"]
    assert (
        run["params"] == {"DEP": "09"}
        and run["options"]["cache"] is True
        and run["options"]["schedule_id"] == sched["id"]
    )
    after = store.get_schedule(sched["id"])
    assert after["last_run_id"] == created[0] and after["last_error"] is None and after["next_run_at"] > now
    assert scheduler.tick(store, now) == []  # plus rien d'échu


def test_not_yet_due_and_disabled_schedules_do_nothing(store):
    flow = store.create_flow("f", FLOW, "alice")
    make_schedule(store, flow["id"], due_in=3600)
    make_schedule(store, flow["id"], enabled=False)
    assert scheduler.tick(store) == []


def test_missed_deadline_beyond_grace_is_skipped_not_caught_up(store):
    flow = store.create_flow("f", FLOW, "alice")
    sched = make_schedule(store, flow["id"], due_in=-7200)  # échéance manquée de 2 h
    assert scheduler.tick(store, grace=3600) == []
    after = store.get_schedule(sched["id"])
    assert "manquée" in after["last_error"] and after["last_run_id"] is None and after["next_run_at"] > time.time()


def test_overlap_is_skipped_unless_allowed(store):
    flow = store.create_flow("f", FLOW, "alice")
    sched = make_schedule(store, flow["id"])
    first = scheduler.tick(store)
    assert len(first) == 1
    store.update_schedule(sched["id"], {**sched, "options": sched["options"]}, time.time() - 1)
    assert scheduler.tick(store) == []  # le run précédent est toujours « queued »
    assert "encore en cours" in store.get_schedule(sched["id"])["last_error"]
    overlap = make_schedule(store, flow["id"], options={"allow_overlap": True})
    assert len(scheduler.tick(store)) == 1  # sans protection : un run de plus
    assert store.get_schedule(overlap["id"])["last_run_id"]


def test_errors_are_recorded_and_the_schedule_keeps_going(store):
    flow = store.create_flow("f", FLOW, "alice")
    gone = make_schedule(store, "0" * 32)
    bad_params = make_schedule(store, flow["id"], params={"INCONNU": "x"})
    bad_cron = make_schedule(store, flow["id"], cron="pas un cron")
    assert scheduler.tick(store) == []
    assert "n'existe plus" in store.get_schedule(gone["id"])["last_error"]
    assert "invalides" in store.get_schedule(bad_params["id"])["last_error"]
    cron_state = store.get_schedule(bad_cron["id"])
    assert "cron invalide" in cron_state["last_error"] and cron_state["next_run_at"] is None


def test_component_definitions_are_pinned_in_scheduled_runs(store):
    from etl_engine import components
    from etl_store import store_components

    components.clear_providers()
    components.install_provider(store_components(store), key="store")
    try:
        store.create_flow(
            "comp",
            {
                "component": {"name": "mon_filtre"},
                "nodes": [{"id": "in", "type": "ComponentInput", "params": {"port": "in"}}],
                "links": [],
            },
            "alice",
        )
        flow = store.create_flow(
            "utilisateur", {"nodes": [{"id": "c", "type": "component:mon_filtre", "params": {}}], "links": []}, "alice"
        )
        sched = make_schedule(store, flow["id"], params={})
        run_id = scheduler.tick(store)[0]
        assert "mon_filtre" in store.get_run(run_id)["flow"]["subflows"]
        assert store.get_schedule(sched["id"])["last_error"] is None
    finally:
        components.clear_providers()


def test_loop_ticks_until_shutdown(store):
    flow = store.create_flow("f", FLOW, "alice")
    sched = make_schedule(store, flow["id"])
    stop = threading.Event()
    thread = threading.Thread(target=scheduler.loop, args=(store, stop, 0.2))
    thread.start()
    deadline = time.time() + 5
    while time.time() < deadline and not store.get_schedule(sched["id"])["last_run_id"]:
        time.sleep(0.05)
    stop.set()
    thread.join(timeout=5)
    assert store.get_schedule(sched["id"])["last_run_id"] and not thread.is_alive()
