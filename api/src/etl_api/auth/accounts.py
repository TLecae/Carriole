"""Comptes locaux du mode serveur (ADR 0052, 0058, 0059) : identifiant et mot de passe vérifiés par Carriole.

Les comptes sont dans un fichier JSON (`ETL_USERS_FILE`), mots de passe hachés par scrypt. Les administrateurs les
gèrent dans *Outils → Comptes* ; le premier se crée au démarrage par `ETL_ADMIN_PASSWORD`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Any

from etl_api.auth.config import AuthConfig
from etl_api.auth.identity import Identity

log = logging.getLogger("etl.auth")


class AuthError(Exception):
    """Connexion refusée ; le message s'affiche tel quel."""


def bad_credentials() -> AuthError:
    """Même réponse pour un compte inconnu et un mauvais mot de passe : rien à deviner."""
    return AuthError("identifiant ou mot de passe incorrect")


_SCRYPT = {"n": 2**15, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, maxmem=2**26, dklen=32, **_SCRYPT)
    b64 = base64.b64encode
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${b64(salt).decode()}${b64(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        expected = base64.b64decode(digest)
        got = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), maxmem=2**26, dklen=32
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, expected)


_DUMMY = hash_password(secrets.token_urlsafe(8))  # un compte inconnu coûte le même temps qu'un mauvais mot de passe


def read_users(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8") or "{}")
    return dict(data.get("users", {}))


def write_users(path: Path, users: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"users": dict(sorted(users.items()))}, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_bytes(text.encode())
    if os.name != "nt":
        tmp.chmod(0o600)
    os.replace(tmp, path)


ACCOUNT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,99}$")
MIN_PASSWORD = 8
_ACCOUNTS_LOCK = threading.Lock()


class AccountError(ValueError):
    """Compte local refusé (nom, mot de passe, doublon) ; le message s'affiche tel quel."""


class LocalAccounts:
    """Les comptes : un fichier JSON, mots de passe hachés par scrypt. L'administrateur les gère depuis Carriole
    (`/api/accounts`) ou par `etl users`."""

    def __init__(self, path: Path) -> None:
        self.path = path

    # ------------------------------------------------------------------------------------ gestion des comptes
    def accounts(self) -> list[dict[str, Any]]:
        return [
            {"name": n, "display": str(e.get("display") or n), "groups": list(e.get("groups", []))}
            for n, e in sorted(read_users(self.path).items(), key=lambda kv: kv[0].lower())
        ]

    def find(self, name: str) -> str | None:
        """Nom exact d'un compte (identifiants sans casse), ou `None`."""
        return next((n for n in read_users(self.path) if n.lower() == name.strip().lower()), None)

    @staticmethod
    def _check_password(password: str) -> None:
        if len(password) < MIN_PASSWORD:
            raise AccountError(f"mot de passe trop court : {MIN_PASSWORD} caractères au moins")

    def create(self, name: str, password: str, display: str = "", groups: list[str] | None = None) -> dict[str, Any]:
        name = name.strip()
        if not ACCOUNT_NAME.match(name):
            raise AccountError("identifiant invalide : lettres, chiffres, « . », « _ », « - », « @ » (100 au plus)")
        self._check_password(password)
        with _ACCOUNTS_LOCK:
            users = read_users(self.path)
            if any(n.lower() == name.lower() for n in users):
                raise AccountError(f"le compte « {name} » existe déjà")
            users[name] = {
                "password": hash_password(password),
                "display": display.strip() or name,
                "groups": _groups(groups),
            }
            write_users(self.path, users)
        return {"name": name, "display": users[name]["display"], "groups": users[name]["groups"]}

    def update(
        self, name: str, display: str | None = None, groups: list[str] | None = None, password: str | None = None
    ) -> dict[str, Any]:
        if password is not None:
            self._check_password(password)
        with _ACCOUNTS_LOCK:
            users = read_users(self.path)
            exact = next((n for n in users if n.lower() == name.lower()), None)
            if exact is None:
                raise KeyError(name)
            entry = users[exact]
            if display is not None:
                entry["display"] = display.strip() or exact
            if groups is not None:
                entry["groups"] = _groups(groups)
            if password is not None:
                entry["password"] = hash_password(password)
            write_users(self.path, users)
        return {"name": exact, "display": entry.get("display") or exact, "groups": list(entry.get("groups", []))}

    def delete(self, name: str) -> str:
        with _ACCOUNTS_LOCK:
            users = read_users(self.path)
            exact = next((n for n in users if n.lower() == name.lower()), None)
            if exact is None:
                raise KeyError(name)
            del users[exact]
            write_users(self.path, users)
        return exact

    def authenticate(self, username: str, password: str) -> Identity:
        username = username.strip()
        users = {k.lower(): (k, v) for k, v in read_users(self.path).items()}
        name, entry = users.get(username.lower(), (username, None))
        if not password or not verify_password(password, entry["password"] if entry else _DUMMY) or entry is None:
            raise bad_credentials()
        groups = tuple(sorted(str(g) for g in entry.get("groups", [])))
        return Identity(name=name, display=str(entry.get("display") or name), groups=groups)


def _groups(groups: list[str] | None) -> list[str]:
    return sorted({g.strip() for g in groups or [] if g and g.strip()})


def bootstrap_admin(config: AuthConfig) -> str | None:
    """Premier démarrage : aucun compte et `ETL_ADMIN_PASSWORD` défini : crée le compte `ETL_ADMIN_USER` (« admin »)
    dans le groupe des administrateurs. Ensuite, la variable ne change plus rien."""
    if not config.admin_password or read_users(config.users_file):
        return None
    group = next((a.removeprefix("group:") for a in config.admins if a.startswith("group:")), None)
    LocalAccounts(config.users_file).create(
        config.admin_user, config.admin_password, groups=[group] if group else [], display=config.admin_user
    )
    log.warning("compte « %s » créé (ETL_ADMIN_PASSWORD) : changer son mot de passe", config.admin_user)
    return config.admin_user
