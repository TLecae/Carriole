"""Sessions du mode serveur : un jeton aléatoire dans un cookie `HttpOnly`, son empreinte dans la base.

Expiration glissante : une session utilisée reste ouverte `hours` heures après sa dernière requête (prolongée au plus
une fois par quart de cette durée, pour ne pas écrire à chaque requête). Un petit cache évite de relire la base à
chaque tuile de la carte ; une déconnexion le vide aussitôt.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from typing import Any

from etl_api.auth.identity import Identity

COOKIE = "etl_session"
CACHE_SECONDS = 20.0


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Sessions:
    def __init__(self, store: Any, hours: float) -> None:
        self.store = store
        self.seconds = max(hours, 0.1) * 3600
        self._cache: dict[str, tuple[float, Identity, float]] = {}  # empreinte -> (lu à, identité, expiration)
        self._lock = threading.Lock()

    def open(self, identity: Identity) -> str:
        token = secrets.token_urlsafe(32)
        self.store.create_session(
            token_hash(token), identity.name, identity.display, list(identity.groups), time.time() + self.seconds
        )
        self.store.record_login(identity.name, identity.display, list(identity.groups))
        return token

    def resolve(self, token: str) -> Identity | None:
        if not token or len(token) > 200:
            return None
        key = token_hash(token)
        now = time.time()
        with self._lock:
            cached = self._cache.get(key)
        if cached and now - cached[0] < CACHE_SECONDS and cached[2] > now:
            return cached[1]
        row = self.store.get_session(key)
        if row is None:
            with self._lock:
                self._cache.pop(key, None)
            return None
        expires = row["expires_at"]
        if expires - now < self.seconds * 0.75:  # utilisée : prolongée
            expires = now + self.seconds
            self.store.extend_session(key, expires)
        identity = Identity(name=row["name"], display=row["display"], groups=tuple(row["groups"]))
        with self._lock:
            if len(self._cache) > 5000:
                self._cache.clear()
            self._cache[key] = (now, identity, expires)
        return identity

    def close(self, token: str) -> None:
        key = token_hash(token)
        with self._lock:
            self._cache.pop(key, None)
        self.store.delete_session(key)

    def close_user(self, name: str) -> None:
        with self._lock:
            for key in [k for k, v in self._cache.items() if v[1].name == name]:
                del self._cache[key]
        self.store.delete_user_sessions(name)
