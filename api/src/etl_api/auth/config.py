"""Réglages du mode serveur et de l'authentification (ADR 0052).

Chaque réglage se lit dans la variable `ETL_<CLÉ>` (en majuscules), sinon dans `settings.json` (`<clé>`), sinon prend
sa valeur par défaut. Les secrets (mot de passe du compte de service LDAP) ne se lisent que dans l'environnement ou
dans un fichier (`ETL_LDAP_BIND_PASSWORD_FILE`) : jamais dans `settings.json`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from etl_engine import settings as user_settings

PROVIDERS = ("ldap", "proxy", "file")
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


@dataclass(frozen=True)
class LdapConfig:
    url: str
    starttls: bool
    ca_file: str
    bind_dn: str
    bind_password: str
    user_base: str
    user_filter: str
    username_attribute: str
    display_attributes: tuple[str, ...]
    group_base: str
    group_filter: str
    group_name_attribute: str
    required_group: str
    timeout: float

    @classmethod
    def load(cls) -> LdapConfig:
        return cls(
            url=str(_get("ldap_url")),
            starttls=_bool("ldap_starttls"),
            ca_file=str(_get("ldap_ca_file")),
            bind_dn=str(_get("ldap_bind_dn")),
            bind_password=_secret("ldap_bind_password"),
            user_base=str(_get("ldap_user_base")),
            user_filter=str(_get("ldap_user_filter", "(uid={username})")),
            username_attribute=str(_get("ldap_username_attribute", "uid")),
            display_attributes=_list("ldap_display_attributes", ("cn",)),
            group_base=str(_get("ldap_group_base")),
            group_filter=str(_get("ldap_group_filter", "(|(member={dn})(uniqueMember={dn})(memberUid={username}))")),
            group_name_attribute=str(_get("ldap_group_name_attribute", "cn")),
            required_group=str(_get("ldap_required_group")),
            timeout=float(_get("ldap_timeout", 10)),
        )

    def problems(self) -> list[str]:
        out = []
        if not self.url.lower().startswith(("ldap://", "ldaps://")):
            out.append("ETL_LDAP_URL : adresse ldap://… ou ldaps://… attendue")
        if not self.user_base:
            out.append("ETL_LDAP_USER_BASE : base de recherche des comptes manquante")
        if "{username}" not in self.user_filter:
            out.append("ETL_LDAP_USER_FILTER : le filtre doit contenir {username}")
        if self.bind_dn and not self.bind_password:
            out.append("ETL_LDAP_BIND_PASSWORD (ou _FILE) : mot de passe du compte de service manquant")
        return out

    def public(self) -> dict[str, Any]:
        """Réglages montrés dans *Paramètres* (sans le mot de passe)."""
        return {
            "url": self.url,
            "starttls": self.starttls,
            "bind_dn": self.bind_dn,
            "bind_password_set": bool(self.bind_password),
            "user_base": self.user_base,
            "user_filter": self.user_filter,
            "group_base": self.group_base,
            "group_filter": self.group_filter,
            "required_group": self.required_group,
        }


@dataclass(frozen=True)
class ProxyConfig:
    header: str
    groups_header: str
    display_header: str

    @classmethod
    def load(cls) -> ProxyConfig:
        return cls(
            header=str(_get("auth_header", "X-Forwarded-User")),
            groups_header=str(_get("auth_groups_header")),
            display_header=str(_get("auth_display_header")),
        )


@dataclass(frozen=True)
class AuthConfig:
    provider: str
    admins: tuple[str, ...]
    session_hours: float
    trusted_proxies: tuple[str, ...]
    users_file: Path
    ldap: LdapConfig
    proxy: ProxyConfig
    secure_cookie: bool

    @classmethod
    def load(cls, meta_dir: Path | None = None) -> AuthConfig:
        meta = meta_dir or user_settings.app_dirs().meta
        return cls(
            provider=str(_get("auth")).lower(),
            admins=_list("admins", DEFAULT_ADMINS),
            session_hours=float(_get("session_hours", 12)),
            trusted_proxies=_list("trusted_proxies", ("127.0.0.1", "::1")),
            users_file=Path(str(_get("users_file", meta / "users.json"))),
            ldap=LdapConfig.load(),
            proxy=ProxyConfig.load(),
            # cookie « Secure » : posé d'office derrière HTTPS ; à forcer quand le proxy ne transmet pas le schéma
            secure_cookie=_bool("secure_cookie"),
        )

    def problems(self) -> list[str]:
        """Ce qui empêche de démarrer en mode serveur (le message dit quoi régler)."""
        if self.provider not in PROVIDERS:
            return [f"ETL_AUTH : fournisseur d'identité attendu ({', '.join(PROVIDERS)}), reçu « {self.provider} »"]
        if self.provider == "ldap":
            return self.ldap.problems()
        if self.provider == "file" and not self.users_file.is_file():
            return [
                f"ETL_USERS_FILE : fichier des comptes introuvable ({self.users_file}) ; le créer par `etl users add`"
            ]
        return []

    def is_admin(self, name: str, groups: tuple[str, ...] | list[str]) -> bool:
        from etl_store import access

        return access.is_admin(name, groups, self.admins)
