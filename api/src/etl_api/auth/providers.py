"""Fournisseurs d'identité du mode serveur (ADR 0052), interchangeables par `ETL_AUTH` :

- `ldap` : identifiant et mot de passe vérifiés par un annuaire LDAP (compte de service pour chercher le compte, puis
  connexion avec le mot de passe de l'utilisateur) ; groupes lus dans l'annuaire, groupe obligatoire facultatif ;
- `proxy` : un proxy d'authentification (SSO) a déjà identifié l'utilisateur et transmet son nom dans un en-tête ;
  l'en-tête n'est cru que s'il vient d'un proxy de confiance (`ETL_TRUSTED_PROXIES`) ;
- `file` : comptes d'un fichier JSON (mots de passe hachés par scrypt), pour une petite équipe sans annuaire.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import ssl
from collections.abc import Callable
from pathlib import Path
from typing import Any

from etl_api.auth.config import AuthConfig, LdapConfig, ProxyConfig
from etl_api.auth.identity import Identity

log = logging.getLogger("etl.auth")


class AuthError(Exception):
    """Connexion refusée ; le message s'affiche tel quel."""


def bad_credentials() -> AuthError:
    """Même réponse pour un compte inconnu et un mauvais mot de passe : rien à deviner."""
    return AuthError("identifiant ou mot de passe incorrect")


def not_allowed() -> AuthError:
    return AuthError("ce compte n'est pas autorisé à utiliser Carriole (groupe requis manquant)")


class AuthUnavailable(Exception):
    """Fournisseur injoignable ou mal réglé (annuaire arrêté, compte de service refusé…)."""


# --------------------------------------------------------------------------------------------------------- LDAP


class LdapProvider:
    """Recherche du compte par le compte de service (ou anonymement), puis connexion avec le mot de passe saisi.

    `connect(user, password)` fabrique la connexion (tests : connexion simulée de ldap3).
    """

    def __init__(self, config: LdapConfig, connect: Callable[[str | None, str | None], Any] | None = None) -> None:
        self.config = config
        self._connect = connect or self._real_connection

    def _real_connection(self, user: str | None, password: str | None) -> Any:
        from ldap3 import NONE, Connection, Server, Tls

        cfg = self.config
        secure = cfg.url.lower().startswith("ldaps://")
        tls = None
        if secure or cfg.starttls:
            tls = Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=cfg.ca_file or None)
        server = Server(cfg.url, use_ssl=secure, tls=tls, connect_timeout=cfg.timeout, get_info=NONE)
        return Connection(server, user=user, password=password, receive_timeout=cfg.timeout, read_only=True)

    def _bind(self, user: str | None, password: str | None) -> Any | None:
        from ldap3.core.exceptions import LDAPException

        conn = self._connect(user, password)
        try:
            if self.config.starttls:
                conn.open()
                if not conn.start_tls():
                    raise AuthUnavailable("annuaire LDAP : StartTLS refusé")
            if conn.bind():
                return conn
        except LDAPException as exc:
            log.warning("annuaire LDAP injoignable : %s", exc)
            raise AuthUnavailable("annuaire LDAP injoignable") from None
        result = getattr(conn, "result", None) or {}
        log.info("connexion LDAP refusée pour %s : %s", user or "(anonyme)", result.get("description", "?"))
        return None

    def authenticate(self, username: str, password: str) -> Identity:
        from ldap3 import SUBTREE
        from ldap3.utils.conv import escape_filter_chars

        cfg = self.config
        username = username.strip()
        # mot de passe vide : un annuaire l'accepte comme connexion anonyme, qui « réussirait »
        if not username or not password:
            raise bad_credentials()
        service = self._bind(cfg.bind_dn or None, cfg.bind_password or None)
        if service is None:
            raise AuthUnavailable("annuaire LDAP : le compte de service est refusé (ETL_LDAP_BIND_DN)")
        try:
            search = cfg.user_filter.replace("{username}", escape_filter_chars(username))
            attributes = list(dict.fromkeys([cfg.username_attribute, *cfg.display_attributes]))
            service.search(cfg.user_base, search, SUBTREE, attributes=attributes, size_limit=2)
            entries = list(service.entries)
            if len(entries) != 1:  # inconnu, ou ambigu : jamais deviner
                raise bad_credentials()
            entry = entries[0]
            dn = entry.entry_dn
            user = self._bind(dn, password)
            if user is None:
                raise bad_credentials()
            user.unbind()
            name = _first(entry, cfg.username_attribute) or username
            display = " ".join(v for v in (_first(entry, a) for a in cfg.display_attributes) if v) or name
            groups = self._groups(service, dn, name)
            if cfg.required_group and not self._in_required(service, dn, name, groups):
                raise not_allowed()
        finally:
            service.unbind()
        return Identity(name=name, display=display, groups=tuple(sorted(groups)))

    def _member_filter(self, dn: str, name: str) -> str:
        from ldap3.utils.conv import escape_filter_chars

        return self.config.group_filter.replace("{dn}", escape_filter_chars(dn)).replace(
            "{username}", escape_filter_chars(name)
        )

    def _groups(self, service: Any, dn: str, name: str) -> set[str]:
        from ldap3 import SUBTREE

        cfg = self.config
        if not cfg.group_base:
            return set()
        service.search(cfg.group_base, self._member_filter(dn, name), SUBTREE, attributes=[cfg.group_name_attribute])
        return {v for v in (_first(e, cfg.group_name_attribute) for e in service.entries) if v}

    def _in_required(self, service: Any, dn: str, name: str, groups: set[str]) -> bool:
        """Groupe requis donné par son nom (parmi les groupes lus) ou par son DN (lu directement)."""
        from ldap3 import BASE

        required = self.config.required_group
        if "=" not in required:
            return required.lower() in {g.lower() for g in groups}
        service.search(required, self._member_filter(dn, name), BASE, attributes=["objectClass"])
        return len(service.entries) == 1

    def check(self) -> dict[str, Any]:
        """Test des réglages (*Paramètres*) : connexion du compte de service et lecture de la base des comptes."""
        from ldap3 import BASE

        service = self._bind(self.config.bind_dn or None, self.config.bind_password or None)
        if service is None:
            return outcome(False, "le compte de service est refusé")
        try:
            service.search(self.config.user_base, "(objectClass=*)", BASE, attributes=["objectClass"])
            found = len(service.entries) == 1
        finally:
            service.unbind()
        return outcome(True, "annuaire joignable") if found else outcome(False, "base des comptes introuvable")


def outcome(ok: bool, message: str) -> dict[str, Any]:
    """Résultat d'un test des réglages ; le message est traduit par la route."""
    return {"ok": ok, "message": message}


def _first(entry: Any, attribute: str) -> str:
    try:
        value = entry[attribute].value
    except (KeyError, IndexError, AttributeError):  # ldap3 lève LDAPKeyError (KeyError) pour un attribut absent
        return ""
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "").strip()


# --------------------------------------------------------------------------------------------------- en-tête


def trusted(peer: str | None, networks: tuple[str, ...]) -> bool:
    """`peer` (adresse de la connexion) fait partie des proxys de confiance (adresses ou réseaux CIDR)."""
    if not peer:
        return False
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    for item in networks:
        try:
            if address in ipaddress.ip_network(item, strict=False):
                return True
        except ValueError:
            continue
    return False


class ProxyProvider:
    def __init__(self, config: ProxyConfig, trusted_proxies: tuple[str, ...]) -> None:
        self.config = config
        self.trusted_proxies = trusted_proxies

    def identify(self, headers: Any, peer: str | None) -> Identity | None:
        if not trusted(peer, self.trusted_proxies):
            return None
        name = (headers.get(self.config.header) or "").strip()[:100]
        if not name:
            return None
        groups: tuple[str, ...] = ()
        if self.config.groups_header:
            raw = headers.get(self.config.groups_header) or ""
            groups = tuple(sorted({g.strip() for g in raw.replace(";", ",").split(",") if g.strip()}))
        display = (headers.get(self.config.display_header) or "").strip() if self.config.display_header else ""
        return Identity(name=name, display=display or name, groups=groups)


# --------------------------------------------------------------------------------------------------- fichier

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


class FileProvider:
    def __init__(self, path: Path) -> None:
        self.path = path

    def authenticate(self, username: str, password: str) -> Identity:
        username = username.strip()
        users = {k.lower(): (k, v) for k, v in read_users(self.path).items()}
        name, entry = users.get(username.lower(), (username, None))
        if not password or not verify_password(password, entry["password"] if entry else _DUMMY) or entry is None:
            raise bad_credentials()
        groups = tuple(sorted(str(g) for g in entry.get("groups", [])))
        return Identity(name=name, display=str(entry.get("display") or name), groups=groups)


def password_provider(config: AuthConfig) -> LdapProvider | FileProvider | None:
    if config.provider == "ldap":
        return LdapProvider(config.ldap)
    if config.provider == "file":
        return FileProvider(config.users_file)
    return None
