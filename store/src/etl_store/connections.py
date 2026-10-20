"""Connexions nommées aux bases : paramètres en base de métadonnées, mot de passe chiffré (Fernet).

Types : `postgres` (PostgreSQL / PostGIS), `oracle` (voir etl_engine.connections). Les réglages propres à un type
iraient dans `options` (aucun type n'en a aujourd'hui).
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from etl_store.models import ConnectionRow
from etl_store.store import NotFound, Store

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\- ]{0,58}[A-Za-z0-9_]$|^[A-Za-z0-9]$")
SSL_MODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")
KINDS = {"postgres": 5432, "oracle": 1521}  # type -> port par défaut


def _options(kind: str, raw: Any) -> dict[str, Any]:
    """Réglages propres au type : aucun type n'en a aujourd'hui, les clés reçues sont ignorées."""
    return {}


def _loads(text: str | None) -> dict[str, Any]:
    try:
        value = json.loads(text or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


class InvalidConnection(ValueError):
    pass


def _public(row: ConnectionRow) -> dict[str, Any]:
    """Description d'une connexion **sans** son mot de passe."""
    return {
        "name": row.name,
        "kind": row.kind,
        "host": row.host,
        "port": row.port,
        "dbname": row.dbname,
        "user": row.user,
        "sslmode": row.sslmode,
        "options": _loads(row.options),
        "has_password": bool(row.secret_enc),
        "owner": row.owner,
        "updated_at": row.updated_at,
    }


class ConnectionStore:
    def __init__(self, store: Store, key: bytes) -> None:
        self.store = store
        self._box = Fernet(key)

    def list(self) -> list[dict[str, Any]]:
        with self.store.session() as s:
            return [_public(r) for r in s.scalars(select(ConnectionRow).order_by(ConnectionRow.name))]

    def get(self, name: str) -> dict[str, Any]:
        with self.store.session() as s:
            row = s.get(ConnectionRow, name)
            if row is None:
                raise NotFound(name)
            return _public(row)

    def save(self, name: str, data: dict[str, Any], password: str | None, owner: str) -> dict[str, Any]:
        """Crée ou met à jour. `password` None = conserver l'actuel ; chaîne vide = l'effacer."""
        if not NAME_RE.match(name):
            raise InvalidConnection("nom invalide : lettres, chiffres, espace, « _ », « - » et « . » (60 max)")
        kind = str(data.get("kind") or "postgres")
        if kind not in KINDS:
            raise InvalidConnection(f"type de base inconnu : {kind}")
        port = int(KINDS[kind] if data.get("port") is None else data["port"])
        sslmode = data.get("sslmode") or "prefer"
        if sslmode not in SSL_MODES:
            raise InvalidConnection(f"sslmode inconnu : {sslmode}")
        options = _options(kind, data.get("options"))
        if not str(data.get("host", "")).strip() or not str(data.get("dbname", "")).strip():
            raise InvalidConnection("hôte et base (ou service) sont obligatoires")
        if not 0 < port < 65536:
            raise InvalidConnection("port invalide")
        now = time.time()
        with self.store.session() as s, s.begin():
            row = s.get(ConnectionRow, name)
            if row is None:
                row = ConnectionRow(name=name, owner=owner, created_at=now)
                s.add(row)
            row.kind = kind
            row.options = json.dumps(options)
            row.host = str(data["host"]).strip()
            row.port = port
            row.dbname = str(data["dbname"]).strip()
            row.user = str(data.get("user", "")).strip()
            row.sslmode = sslmode
            if password is not None:
                row.secret_enc = self._box.encrypt(password.encode()).decode() if password else ""
            row.updated_at = now
            return _public(row)

    def delete(self, name: str) -> None:
        with self.store.session() as s, s.begin():
            row = s.get(ConnectionRow, name)
            if row is None:
                raise NotFound(name)
            s.delete(row)

    def _password(self, row: ConnectionRow) -> str:
        if not row.secret_enc:
            return ""
        try:
            return self._box.decrypt(row.secret_enc.encode()).decode()
        except InvalidToken:
            raise InvalidConnection(
                f"mot de passe de « {row.name} » illisible : la clé de chiffrement (ETL_SECRET_KEY) a changé, "
                "ressaisir le mot de passe"
            ) from None

    def provider(self) -> Callable[[str], Any]:
        """Fournisseur pour `etl_engine.connections.install_provider`."""
        from etl_engine.connections import ConnectionInfo
        from etl_engine.sdk import ConfigError

        def resolve(name: str) -> ConnectionInfo | None:
            with self.store.session() as s:
                row = s.get(ConnectionRow, name)
                if row is None:
                    return None
                try:
                    password = self._password(row)
                except InvalidConnection as exc:
                    raise ConfigError(str(exc)) from None
                return ConnectionInfo(
                    name=row.name,
                    host=row.host,
                    port=row.port,
                    dbname=row.dbname,
                    user=row.user,
                    password=password,
                    sslmode=row.sslmode,
                    kind=row.kind or "postgres",
                    options=_loads(row.options),
                )

        return resolve
