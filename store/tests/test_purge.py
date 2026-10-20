import os
import time

import pytest
from etl_store import Store
from etl_store.purge import DAY, ORPHAN_AGE, purge_cache, release_cache
from etl_store.store import SCHEMA_VERSION
from sqlalchemy import text

FLOW = {"name": "f", "nodes": [], "links": []}
MB = 2**20


@pytest.fixture
def store(tmp_path):
    return Store(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")


def make_run(store, cache, size_mb, age_days, status="done", options=None, now=None):
    now = now or time.time()
    run = store.create_run(FLOW, None, {}, options or {}, "alice")
    if status != "queued":
        with store.engine.begin() as con:
            con.execute(
                text("UPDATE runs SET status=:s, finished_at=:f, started_at=:f WHERE id=:i"),
                {"s": status, "f": now - age_days * DAY, "i": run["id"]},
            )
    d = cache / run["id"] / "node"
    d.mkdir(parents=True)
    (d / "output.parquet").write_bytes(b"x" * int(size_mb * MB))
    return run["id"]


def test_old_runs_are_purged_and_recent_ones_kept(store, tmp_path):
    cache = tmp_path / "cache"
    old = make_run(store, cache, 1, age_days=30)
    recent = make_run(store, cache, 1, age_days=2)
    report = purge_cache(store, cache, retention_days=14, max_bytes=10_000 * MB)
    assert report.deleted == [old] and report.reasons[old] == "ancienneté"
    assert not (cache / old).exists() and (cache / recent).exists()
    assert store.get_run(old)["purged_at"] is not None and store.get_run(recent)["purged_at"] is None
    assert purge_cache(store, cache, 14, 10_000 * MB).deleted == []  # déjà fait : rien à refaire


def test_size_limit_removes_oldest_first(store, tmp_path):
    cache = tmp_path / "cache"
    ids = [make_run(store, cache, 2, age_days=d) for d in (5, 4, 3, 2, 1)]  # 10 Mo en tout
    report = purge_cache(store, cache, retention_days=365, max_bytes=5 * MB)
    assert report.deleted == ids[:3] and set(report.reasons.values()) == {"taille"}
    assert report.remaining_bytes <= 5 * MB and report.freed_bytes == 6 * MB
    assert all((cache / i).exists() for i in ids[3:])


def test_running_queued_and_resumed_runs_are_protected(store, tmp_path):
    cache = tmp_path / "cache"
    base = make_run(store, cache, 1, age_days=60)  # vieux, mais un run en cours le reprend
    other = make_run(store, cache, 1, age_days=60)
    running = make_run(store, cache, 1, age_days=60, status="queued", options={"reuse_from": base})
    store.claim_next_run("w")  # le run en file passe « en cours » et reprend `base`
    queued = make_run(store, cache, 1, age_days=0, status="queued")
    finished_today = make_run(store, cache, 1, age_days=0)
    # limite de taille nulle : tout ce qui n'est pas protégé doit partir, même le récent
    report = purge_cache(store, cache, retention_days=14, max_bytes=0)
    assert set(report.deleted) == {other, finished_today}
    assert all((cache / r).exists() for r in (base, running, queued))


def test_dry_run_changes_nothing(store, tmp_path):
    cache = tmp_path / "cache"
    old = make_run(store, cache, 1, age_days=30)
    report = purge_cache(store, cache, 14, 10_000 * MB, dry_run=True)
    assert report.deleted == [old] and (cache / old).exists() and store.get_run(old)["purged_at"] is None


def test_orphan_directories_and_old_exports(store, tmp_path):
    cache = tmp_path / "cache"
    (cache / "orpheline").mkdir(parents=True)
    (cache / "orpheline" / "f").write_bytes(b"x")
    (cache / "_exports" / "ancien").mkdir(parents=True)
    (cache / "_exports" / "recent").mkdir(parents=True)
    old = time.time() - 3 * DAY
    for p in (cache / "orpheline", cache / "_exports", cache / "_exports" / "ancien"):
        os.utime(p, (old, old))
    report = purge_cache(store, cache, 14, 10_000 * MB)
    assert report.orphans == ["orpheline"] and not (cache / "orpheline").exists()
    assert not (cache / "_exports" / "ancien").exists() and (cache / "_exports" / "recent").exists()


def test_staging_keeps_what_is_still_being_written_and_drops_abandoned_work(store, tmp_path):
    """`_staging` : écritures en cours vers un partage. Un travail abandonné (worker tué) disparaît après un jour ;
    un fichier encore en cours d'écriture, dans un dossier dont la date n'a pas bougé, reste."""
    cache = tmp_path / "cache"
    abandoned, active = cache / "_staging" / "abandonne", cache / "_staging" / "en_cours"
    for d in (abandoned, active):
        d.mkdir(parents=True)
        (d / "data.gpkg").write_bytes(b"x")
    old = time.time() - 3 * DAY
    for p in (cache / "_staging", abandoned, abandoned / "data.gpkg", active):  # `active` : dossier ancien...
        os.utime(p, (old, old))
    # ... mais son fichier vient d'être écrit
    purge_cache(store, cache, 14, 10_000 * MB)
    assert not abandoned.exists()
    assert (active / "data.gpkg").exists()


def test_periodic_lock_is_granted_once_per_interval(store):
    assert store.try_acquire("purge", 3600) is True
    assert store.try_acquire("purge", 3600) is False
    assert store.try_acquire("purge", 0) is True  # période écoulée


def test_migration_adds_purged_at_to_a_v1_database(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE meta (key VARCHAR(50) PRIMARY KEY, value VARCHAR(200));
        INSERT INTO meta VALUES ('schema_version', '1');
        CREATE TABLE runs (id VARCHAR(32) PRIMARY KEY, flow_id VARCHAR(32), flow_name VARCHAR(200), flow_json TEXT,
          params TEXT, options TEXT, status VARCHAR(20), owner VARCHAR(100), created_at FLOAT, started_at FLOAT,
          finished_at FLOAT, heartbeat_at FLOAT, worker VARCHAR(100), error TEXT, result TEXT,
          cancel_requested BOOLEAN);
        INSERT INTO runs (id, flow_name, flow_json, params, options, status, owner, created_at)
          VALUES ('r1', 'ancien', '{}', '{}', '{}', 'done', 'bob', 1.0);
        """
    )
    con.commit()
    con.close()
    store = Store(f"sqlite:///{path.as_posix()}")
    run = store.get_run("r1")
    assert run["flow_name"] == "ancien" and run["purged_at"] is None
    store.mark_purged(["r1"])
    assert store.get_run("r1")["purged_at"] is not None
    with store.engine.begin() as c:
        assert c.execute(text("SELECT value FROM meta WHERE key='schema_version'")).scalar() == str(SCHEMA_VERSION)


def test_desktop_keeps_only_running_runs_and_the_ones_shown_in_an_open_flow(store, tmp_path):
    """Poste de bureau : un flux fermé (ou l'application plantée) ne laisse pas son cache derrière lui."""
    cache = tmp_path / "cache"
    shown = make_run(store, cache, 1, age_days=0)  # le run affiché dans l'éditeur
    closed = make_run(store, cache, 1, age_days=0)  # un flux fermé, même tout récent
    base = make_run(store, cache, 1, age_days=0)
    make_run(store, cache, 1, age_days=0, status="queued", options={"reuse_from": base})  # en attente, reprend `base`
    orphan_old, orphan_new = cache / "reste-plantage", cache / "en-creation"
    for folder in (orphan_old, orphan_new):
        folder.mkdir()
    os.utime(orphan_old, (time.time() - 2 * ORPHAN_AGE,) * 2)

    report = release_cache(store, cache, held={shown})
    assert report.deleted == [closed] and report.reasons[closed] == "flux fermé" and report.freed_bytes >= MB
    assert not (cache / closed).exists() and store.get_run(closed)["purged_at"] is not None
    assert (cache / shown).exists() and (cache / base).exists()  # affiché ; repris par un run en attente
    assert report.orphans == ["reste-plantage"] and orphan_new.exists()

    assert release_cache(store, cache, held=set()).deleted == [shown]  # page fermée : son run part aussi
