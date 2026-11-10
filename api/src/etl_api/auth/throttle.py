"""Freine les essais de mots de passe : après `free` échecs pour un même compte ou une même adresse, chaque nouvel
essai attend un délai qui double (jusqu'à `cap` secondes). Une connexion réussie remet le compte à zéro."""

from __future__ import annotations

import threading
import time


class Throttle:
    def __init__(self, free: int = 5, base: float = 2.0, cap: float = 900.0) -> None:
        self.free = free
        self.base = base
        self.cap = cap
        self._failures: dict[str, tuple[int, float]] = {}  # clé -> (échecs, essai suivant permis à)
        self._lock = threading.Lock()

    def wait(self, *keys: str) -> float:
        """Secondes à attendre avant un nouvel essai (0 : permis)."""
        now = time.monotonic()
        with self._lock:
            return max((self._failures.get(k, (0, 0.0))[1] - now for k in keys), default=0.0) if keys else 0.0

    def fail(self, *keys: str) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._failures) > 50_000:  # attaque massive : on oublie les plus anciens plutôt que de grossir
                self._failures.clear()
            for key in keys:
                count = self._failures.get(key, (0, 0.0))[0] + 1
                delay = 0.0 if count <= self.free else min(self.cap, self.base * 2 ** (count - self.free - 1))
                self._failures[key] = (count, now + delay)

    def succeed(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._failures.pop(key, None)
