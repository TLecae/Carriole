"""Protection de l'API locale (poste de bureau) : jeton, hôtes acceptés, en-têtes de sécurité.

L'API exécute du Python (plugins, PythonScript) et lit les fichiers de l'utilisateur : sur un poste, n'importe quelle
page web ouverte dans un navigateur pourrait l'appeler sur 127.0.0.1. Trois barrières :
- le jeton, propre à chaque démarrage, posé en cookie `HttpOnly; SameSite=Strict` par l'application (ou transmis
  dans l'en-tête `X-ETL-Token` par la ligne de commande) ;
- le contrôle de l'en-tête `Host` (un site malveillant qui détourne son nom DNS vers 127.0.0.1 envoie un autre nom) ;
- aucune ouverture CORS.

Sans jeton configuré (serveur derrière un proxy d'authentification, tests), le contrôle est inactif ; les en-têtes de
sécurité s'appliquent toujours.
"""

from __future__ import annotations

import hmac
import os
from http.cookies import SimpleCookie

from etl_engine import settings as user_settings
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

COOKIE = "etl_token"
HEADER = "x-etl-token"
PUBLIC_PATHS = frozenset({"/api/health"})

# Fonds de plan : MapLibre charge les tuiles par fetch() dans un worker (connect-src) puis les affiche (img-src).
# Le style inline et les workers blob: sont ceux de React Flow et de MapLibre.
TILE_HOSTS = ("https://tile.openstreetmap.org", "https://server.arcgisonline.com", "https://data.geopf.fr")


def content_security_policy() -> str:
    """Politique de contenu. Un serveur de tuiles interne s'ajoute par `ETL_TILE_HOSTS` (adresses séparées par des
    espaces) ou par le réglage `tile_hosts` (liste), sans toucher au code."""
    extra = os.environ.get("ETL_TILE_HOSTS", "").split() + [str(h) for h in user_settings.get("tile_hosts", [])]
    hosts = " ".join(dict.fromkeys([*TILE_HOSTS, *extra]))
    return (
        f"default-src 'self'; img-src 'self' data: blob: {hosts}; style-src 'self' 'unsafe-inline'; "
        f"script-src 'self'; worker-src 'self' blob:; child-src 'self' blob:; connect-src 'self' {hosts}; "
        "font-src 'self' data:; frame-ancestors 'none'"
    )


def security_headers() -> dict[str, str]:
    return {
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        # Les serveurs de tuiles (OpenStreetMap) exigent de savoir quel site les appelle.
        "referrer-policy": "strict-origin-when-cross-origin",
        "content-security-policy": content_security_policy(),
    }


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


class LocalGuard:
    """Middleware ASGI pur (pas de `BaseHTTPMiddleware` : il casse les flux SSE longs)."""

    def __init__(self, app: ASGIApp, token: str = "", hosts: tuple[str, ...] = ()) -> None:
        self.app = app
        self.token = token
        self.hosts = frozenset(h.lower() for h in hosts)

    def _authorized(self, headers: Headers) -> bool:
        if not self.token:
            return True
        if _same(headers.get(HEADER, ""), self.token):
            return True
        cookie = SimpleCookie(headers.get("cookie", ""))
        return COOKIE in cookie and _same(cookie[COOKIE].value, self.token)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        if self.hosts and headers.get("host", "").lower() not in self.hosts:
            await PlainTextResponse("hôte non autorisé", status_code=403)(scope, receive, send)
            return
        if self.token and scope["path"] not in PUBLIC_PATHS and not self._authorized(headers):
            query = scope.get("query_string", b"").decode("latin-1")
            given = dict(p.split("=", 1) for p in query.split("&") if "=" in p).get("token", "")
            if scope["method"] == "GET" and scope["path"] == "/" and given and _same(given, self.token):
                response = RedirectResponse("/", status_code=303)  # connexion : jeton → cookie, puis page d'accueil
                response.set_cookie(COOKIE, self.token, httponly=True, samesite="strict", path="/")
                await response(scope, receive, send)
                return
            await PlainTextResponse("accès refusé", status_code=401)(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                out = MutableHeaders(scope=message)
                for (
                    name,
                    value,
                ) in security_headers().items():  # relus à chaque réponse : `tile_hosts` s'applique de suite
                    out.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)
