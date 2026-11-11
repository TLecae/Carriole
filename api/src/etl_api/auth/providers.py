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
import re
import secrets
import ssl
import threading
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
        # délais en secondes entières : sous Linux, ldap3 les passe à setsockopt (struct) et refuse un nombre décimal
        seconds = max(1, round(cfg.timeout))
        server = Server(cfg.url, use_ssl=secure, tls=tls, connect_timeout=seconds, get_info=NONE)
        return Connection(server, user=user, password=password, receive_timeout=seconds, read_only=True)

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
        except Exception:  # noqa: BLE001 - erreur imprévue du client LDAP : jamais une erreur 500 à la connexion
            log.exception("annuaire LDAP : erreur inattendue")
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


ACCOUNT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,99}$")
MIN_PASSWORD = 8
_ACCOUNTS_LOCK = threading.Lock()


class AccountError(ValueError):
    """Compte local refusé (nom, mot de passe, doublon) ; le message s'affiche tel quel."""


class FileProvider:
    """Comptes locaux : un fichier JSON, mots de passe hachés par scrypt. L'administrateur les gère depuis Carriole
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


class ChainProvider:
    """Plusieurs fournisseurs dans l'ordre de `ETL_AUTH` (ex. « ldap,file ») : le premier qui reconnaît le compte
    l'emporte. Un annuaire injoignable n'empêche pas les comptes locaux (l'administrateur garde la main)."""

    def __init__(self, providers: list[LdapProvider | FileProvider]) -> None:
        self.providers = providers

    def authenticate(self, username: str, password: str) -> Identity:
        refused: AuthError | None = None
        unavailable: AuthUnavailable | None = None
        for provider in self.providers:
            try:
                return provider.authenticate(username, password)
            except AuthError as exc:
                if refused is None or "groupe requis" in str(exc):  # la raison la plus précise
                    refused = exc
            except AuthUnavailable as exc:
                unavailable = exc
        if unavailable is not None and (refused is None or "groupe requis" not in str(refused)):
            raise unavailable
        raise refused or bad_credentials()


def password_provider(config: AuthConfig) -> LdapProvider | FileProvider | ChainProvider | None:
    found: list[LdapProvider | FileProvider] = []
    for name in config.providers:
        if name == "ldap":
            found.append(LdapProvider(config.ldap))
        elif name == "file":
            found.append(FileProvider(config.users_file))
    if not found:
        return None
    return found[0] if len(found) == 1 else ChainProvider(found)


def part(passwords: Any, kind: type) -> Any:
    """Le fournisseur de ce type, seul ou dans une chaîne (comptes locaux, annuaire)."""
    if isinstance(passwords, kind):
        return passwords
    return next((p for p in getattr(passwords, "providers", []) if isinstance(p, kind)), None)


def bootstrap_admin(config: AuthConfig) -> str | None:
    """Premier démarrage : aucun compte local et `ETL_ADMIN_PASSWORD` défini : crée le compte `ETL_ADMIN_USER`
    (« admin ») dans le groupe des administrateurs. Ensuite, la variable ne change plus rien."""
    if "file" not in config.providers or not config.admin_password or read_users(config.users_file):
        return None
    group = next((a.removeprefix("group:") for a in config.admins if a.startswith("group:")), None)
    FileProvider(config.users_file).create(
        config.admin_user, config.admin_password, groups=[group] if group else [], display=config.admin_user
    )
    log.warning("compte local « %s » créé (ETL_ADMIN_PASSWORD) : changer son mot de passe", config.admin_user)
    return config.admin_user
