"""Cache gardé tant que son flux est ouvert (poste de bureau, `cache_policy = "session"`).

Chaque page de l'interface signale toutes les 20 s les runs qu'elle affiche (le run courant de l'éditeur : inspecteur
et reprise des nœuds inchangés). Le balayage supprime le cache des autres runs terminés (`release_cache`) :

- flux fermé ou remplacé dans l'éditeur : son run n'est plus signalé ;
- fenêtre fermée : la page se retire (`release`), ou cesse de se signaler (plantage) et expire après `ttl` ;
- moteur relancé après un plantage : rien n'est supprimé pendant `ttl`, le temps que les pages se signalent à nouveau.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from pathlib import Path

from etl_store import Store
from etl_store.purge import release_cache

log = logging.getLogger("etl.cache")
SWEEP_INTERVAL = 15.0


class Holds:
    def __init__(self, ttl: float) -> None:
        self.ttl = ttl
        self.started = time.monotonic()
        self._pages: dict[str, tuple[set[str], float]] = {}
        self._lock = threading.Lock()

    def hold(self, page: str, runs: list[str]) -> None:
        with self._lock:
            self._pages[page] = ({r for r in runs if r}, time.monotonic())

    def release(self, page: str) -> None:
        with self._lock:
            self._pages.pop(page, None)

    def held(self) -> set[str]:
        """Runs affichés par les pages qui se sont signalées depuis moins de `ttl` secondes."""
        now = time.monotonic()
        with self._lock:
            for page in [p for p, (_, seen) in self._pages.items() if now - seen > self.ttl]:
                del self._pages[page]
            return set().union(*(runs for runs, _ in self._pages.values()))

    def ready(self) -> bool:
        """Faux pendant `ttl` secondes après le démarrage : les pages ouvertes n'ont pas encore pu se signaler."""
        return time.monotonic() - self.started >= self.ttl


def sweep(store: Store, cache_dir: Path, holds: Holds) -> None:
    if holds.ready():
        release_cache(store, cache_dir, holds.held())


async def sweep_loop(store: Store, cache_dir: Path, holds: Holds, interval: float = SWEEP_INTERVAL) -> None:
    while True:
        await asyncio.sleep(interval)
        with contextlib.suppress(Exception):  # disque occupé, base verrouillée… : au prochain passage
            await asyncio.to_thread(sweep, store, cache_dir, holds)
