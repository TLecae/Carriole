"""Réglages du mode serveur et de l'authentification par comptes locaux (ADR 0052, 0059).

Chaque réglage se lit dans la variable `ETL_<CLÉ>` (en majuscules), sinon dans `settings.json` (`<clé>`), sinon prend
sa valeur par défaut. Le mot de passe du premier administrateur ne se lit que dans l'environnement ou dans un fichier
(`ETL_ADMIN_PASSWORD_FILE`) : jamais dans `settings.json`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from etl_engine import settings as user_settings

DEFAULT_ADMINS = ("group:carriole-admins",)


def server_mode() -> bool:
    """Mode serveur : posé par `etl serve --server` (hérité par les workers)."""
    return os.environ.get("ETL_MODE", "").strip().lower() == "server"


def _get(key: str, default: Any = "") -> Any:
    env = os.environ.get(f"ETL_{key.upper()}")
    if env is not None and env.strip() != "":
        return env.strip()
    value = user_settings.get(key)
    return default if value in (None, "", []) else value


def _list(key: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    value = _get(key, None)
    if value is None:
        return default
    items = value.split(",") if isinstance(value, str) else [str(v) for v in value]
    return tuple(i.strip() for i in items if i.strip())


def _bool(key: str, default: bool = False) -> bool:
    value = _get(key, None)
    if value is None:
        return default
    return value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "oui", "yes", "on")


def _secret(key: str) -> str:
    """`ETL_<CLÉ>_FILE` (fichier de secret, ex. secret Docker) de préférence, sinon `ETL_<CLÉ>`."""
    path = os.environ.get(f"ETL_{key.upper()}_FILE", "").strip()
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    return os.environ.get(f"ETL_{key.upper()}", "")


def _has_accounts(path: Path) -> bool:
    try:
        return bool(json.loads(path.read_text(encoding="utf-8") or "{}").get("users"))
    except (OSError, ValueError):
        return False


@dataclass(frozen=True)
class AuthConfig:
    admins: tuple[str, ...]
    session_hours: float
    # proxy HTTPS placé devant le serveur : seul lui transmet l'adresse du client et le schéma (X-Forwarded-…)
    trusted_proxies: tuple[str, ...]
    users_file: Path
    secure_cookie: bool
    admin_user: str  # premier compte, créé au démarrage s'il n'y en a aucun (ETL_ADMIN_PASSWORD)
    admin_password: str

    @classmethod
    def load(cls, meta_dir: Path | None = None) -> AuthConfig:
        meta = meta_dir or user_settings.app_dirs().meta
        return cls(
            admins=_list("admins", DEFAULT_ADMINS),
            session_hours=float(_get("session_hours", 12)),
            trusted_proxies=_list("trusted_proxies", ("127.0.0.1", "::1")),
            users_file=Path(str(_get("users_file", meta / "users.json"))),
            # cookie « Secure » : posé d'office derrière HTTPS ; à forcer quand le proxy ne transmet pas le schéma
            secure_cookie=_bool("secure_cookie"),
            admin_user=str(_get("admin_user", "admin")),
            admin_password=_secret("admin_password"),
        )

    def problems(self) -> list[str]:
        """Ce qui empêche de démarrer en mode serveur (le message dit quoi régler)."""
        if not self.admin_password and not _has_accounts(self.users_file):
            return [
                f"aucun compte ({self.users_file}) : créer le premier administrateur par ETL_ADMIN_PASSWORD "
                "(ou ETL_ADMIN_PASSWORD_FILE), ou par `etl users add`"
            ]
        return []

    def is_admin(self, name: str, groups: tuple[str, ...] | list[str]) -> bool:
        from etl_store import access

        return access.is_admin(name, groups, self.admins)
