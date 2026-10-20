"""Poste de bureau : le cache d'un run n'est gardé que tant qu'une page ouverte l'affiche."""

from etl_api import cache_hold
from etl_api.cache_hold import Holds
from etl_api.main import create_app
from etl_store import Settings
from fastapi.testclient import TestClient


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_holds_expire_without_signal_and_nothing_is_freed_right_after_a_restart(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(cache_hold.time, "monotonic", clock)
    holds = Holds(ttl=90)
    holds.hold("page-a", ["r1", ""])
    holds.hold("page-b", ["r2"])
    assert holds.held() == {"r1", "r2"} and not holds.ready()  # moteur relancé : les pages ont 90 s pour se signaler
    clock.now += 60
    holds.hold("page-a", ["r3"])  # l'éditeur a changé de flux
    holds.release("page-b")  # fenêtre fermée
    assert holds.held() == {"r3"}
    clock.now += 91  # page-a ne se signale plus : plantage
    assert holds.held() == set() and holds.ready()


def test_pages_hold_and_release_through_the_api(tmp_path):
    settings = Settings(
        meta_dir=tmp_path / "meta",
        cache_dir=tmp_path / "cache",
        plugins_dir=tmp_path / "plugins",
        cache_policy="session",
        cache_hold_ttl=60.0,
    )
    with TestClient(create_app(settings)) as client:
        store = client.app.state.store
        run = store.create_run({"name": "f", "nodes": [], "links": []}, None, {}, {}, "alice")
        assert client.post("/api/cache/hold", json={"page": "p1", "runs": [run["id"]]}).json() == {"policy": "session"}
        assert client.app.state.holds.held() == {run["id"]}
        # fermeture de la page : `navigator.sendBeacon`, corps en texte brut
        r = client.post("/api/cache/release", content='{"page": "p1"}', headers={"Content-Type": "text/plain"})
        assert r.status_code == 200 and client.app.state.holds.held() == set()
        assert client.post("/api/cache/release", content="n'importe quoi").status_code == 422
