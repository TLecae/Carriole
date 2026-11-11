"""Service d'authentification du mode serveur : qui fait la requête, et le middleware qui l'exige (ADR 0052)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from http.cookies import CookieError, SimpleCookie
from typing import Any
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from etl_api.auth.config import AuthConfig
from etl_api.auth.identity import Identity
from etl_api.auth.providers import (
    ChainProvider,
    FileProvider,
    LdapProvider,
    ProxyProvider,
    bootstrap_admin,
    part,
    password_provider,
    trusted,
)
from etl_api.auth.sessions import COOKIE, Sessions
from etl_api.auth.throttle import Throttle

# sans session : la santé (supervision), la page de connexion et ce qu'il lui faut ; l'interface statique (code
# public) se charge aussi sans session, elle affiche alors la page de connexion
PUBLIC_API = frozenset({"/api/health", "/api/auth/info", "/api/auth/login", "/api/auth/logout"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass
class AuthService:
    config: AuthConfig
    sessions: Sessions
    passwords: LdapProvider | FileProvider | ChainProvider | None = None
    proxy: ProxyProvider | None = None
    throttle: Throttle = field(default_factory=Throttle)

    @classmethod
    def create(cls, config: AuthConfig, store: Any) -> AuthService:
        proxy = ProxyProvider(config.proxy, config.trusted_proxies) if "proxy" in config.providers else None
        bootstrap_admin(config)  # premier démarrage : compte administrateur local (ETL_ADMIN_PASSWORD)
        return cls(config, Sessions(store, config.session_hours), password_provider(config), proxy)

    @property
    def accounts(self) -> FileProvider | None:
        """Comptes locaux gérés par l'administrateur (`file` dans `ETL_AUTH`), ou `None`."""
        return part(self.passwords, FileProvider)

    @property
    def ldap(self) -> LdapProvider | None:
        return part(self.passwords, LdapProvider)

    def with_role(self, identity: Identity) -> Identity:
        """Le rôle se recalcule à chaque requête : un changement de `ETL_ADMINS` s'applique sans reconnexion."""
        return replace(identity, admin=self.config.is_admin(identity.name, identity.groups))

    def identify(self, headers: Headers, peer: str | None) -> Identity | None:
        if self.proxy is not None:
            found = self.proxy.identify(headers, peer)
        else:
            found = self.sessions.resolve(session_token(headers))
        return self.with_role(found) if found else None


def session_token(headers: Headers) -> str:
    try:
        cookie = SimpleCookie(headers.get("cookie", ""))
    except CookieError:
        return ""
    return cookie[COOKIE].value if COOKIE in cookie else ""


def _same_origin(headers: Headers) -> bool:
    """Une écriture venue d'une autre page (formulaire piégé) est refusée ; sans `Origin` (ligne de commande,
    supervision), le cookie `SameSite=Strict` suffit."""
    origin = headers.get("origin")
    if origin is None:
        return True
    return bool(origin) and urlsplit(origin).netloc.lower() == headers.get("host", "").lower()


async def deny(send: Send, headers: Headers, status: int, detail: str) -> None:
    """Réponse d'erreur JSON du middleware, dans la langue du navigateur."""
    from etl_engine import i18n

    lang = i18n.normalize(headers.get("accept-language"))
    body = json.dumps({"detail": i18n.tr(detail, lang)}, ensure_ascii=False).encode()
    out = [(b"content-type", b"application/json; charset=utf-8"), (b"content-length", str(len(body)).encode())]
    await send({"type": "http.response.start", "status": status, "headers": out})
    await send({"type": "http.response.body", "body": body})


class ServerGuard:
    """Middleware ASGI pur (comme `LocalGuard` : `BaseHTTPMiddleware` casse les flux SSE longs).

    Pose `state.identity`, `state.client_ip` (adresse du client, derrière un proxy de confiance : la sienne d'après
    `X-Forwarded-For`) et `state.secure` (HTTPS, d'après `X-Forwarded-Proto` derrière un proxy de confiance).
    """

    def __init__(self, app: ASGIApp, hosts: tuple[str, ...] = ()) -> None:
        self.app = app
        self.hosts = frozenset(h.lower() for h in hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from etl_api.security import security_headers

        headers = Headers(scope=scope)
        if self.hosts and headers.get("host", "").lower() not in self.hosts:
            await deny(send, headers, 403, "hôte non autorisé")
            return
        service: AuthService = scope["app"].state.auth
        peer = (scope.get("client") or (None, None))[0]
        client_ip, secure = peer, scope.get("scheme") == "https"
        if trusted(peer, service.config.trusted_proxies):
            if forwarded := headers.get("x-forwarded-for"):
                client_ip = forwarded.split(",")[-1].strip() or peer
            if proto := headers.get("x-forwarded-proto"):
                secure = proto.split(",")[0].strip().lower() == "https"
        state = scope.setdefault("state", {})
        state["client_ip"] = client_ip
        state["secure"] = secure or service.config.secure_cookie
        path = scope["path"]
        if scope["method"] not in SAFE_METHODS and not _same_origin(headers):
            await deny(send, headers, 403, "origine de la requête refusée")
            return
        identity = service.identify(headers, peer)
        if identity is not None:
            state["identity"] = identity
        protected = (path.startswith("/api/") and path not in PUBLIC_API) or path.startswith("/tiles/")
        if protected and identity is None:
            await deny(send, headers, 401, "authentification requise")
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                out = MutableHeaders(scope=message)
                for name, value in security_headers().items():
                    out.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)
