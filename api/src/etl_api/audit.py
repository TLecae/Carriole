"""Journal d'activité du mode serveur (ADR 0056) : qui a fait quoi, sur quoi, quand et d'où.

Les routes d'écriture listées dans `ACTIONS` (par le nom de leur fonction) sont suivies par `AuditMiddleware` :
chaque succès, et chaque échec de connexion, ajoute une ligne. Jamais de mot de passe ni de contenu de flux : l'objet,
et pour les réglages et les partages, ce qui a changé.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from etl_api.auth import server_mode

log = logging.getLogger("etl.audit")

# nom de la fonction de la route -> action consignée (code stable, traduit par l'interface)
ACTIONS = {
    "login": "login",
    "logout": "logout",
    "create_flow": "flow.create",
    "update_flow": "flow.update",
    "delete_flow": "flow.delete",
    "set_flow_shares": "flow.share",
    "save_connection": "connection.save",
    "delete_connection": "connection.delete",
    "set_connection_shares": "connection.share",
    "create_run": "run.create",
    "create_batch": "run.batch",
    "retry_run": "run.retry",
    "cancel_run": "run.cancel",
    "pause_run": "run.pause",
    "resume_run": "run.resume",
    "create_schedule": "schedule.create",
    "update_schedule": "schedule.update",
    "delete_schedule": "schedule.delete",
    "run_now": "schedule.run",
    "patch_settings": "settings.update",
    "put_provider": "assistant.provider",
    "delete_provider": "assistant.provider",
    "put_default": "assistant.provider",
    "install_modifier": "assistant.modifier",
    "publish_version": "component.publish",
    "shutdown": "server.stop",
}
TARGET_PARAMS = ("flow_id", "run_id", "schedule_id", "name", "provider_id")


def _json(source: bytes) -> dict[str, Any]:
    try:
        data = json.loads(source) if source else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def entry(scope: Scope, action: str, status: int, sent: bytes, received: bytes) -> tuple[str, str, str, dict, str]:
    """(utilisateur, action, objet, détail, adresse) d'une requête terminée."""
    body, answer = _json(received), _json(sent)
    state = scope.get("state") or {}
    identity = state.get("identity")
    user = identity.name if identity else str(answer.get("name") or body.get("username") or "")[:100]
    params = scope.get("path_params") or {}
    target = next((str(params[p]) for p in TARGET_PARAMS if p in params), "")
    if not target:
        target = str(answer.get("id") or answer.get("batch") or "")
    detail: dict[str, Any] = {}
    if action == "login" and status >= 400:
        action = "login.failed"
        detail["status"] = status
    elif action == "settings.update":
        detail["keys"] = sorted(body)
    elif action.endswith(".share"):
        detail["shares"] = body.get("shares", [])
    elif action in ("flow.create", "flow.update"):
        detail["name"] = (body.get("flow") or {}).get("name", "")
    elif action == "run.create" and answer.get("flow_name"):
        detail["flow"] = answer["flow_name"]
    return user, action, target, detail, str(state.get("client_ip") or "")


class AuditMiddleware:
    """Middleware ASGI pur, placé sous `ServerGuard` (l'identité est déjà posée) : après une écriture réussie d'une
    route de `ACTIONS` (ou un échec de connexion), ajoute une ligne au journal."""

    LIMIT = 256 * 1024  # corps lus pour le détail (réglages, partages) ; au-delà, ignorés

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS") or not server_mode():
            await self.app(scope, receive, send)
            return
        received: list[bytes] = []
        sent: list[bytes] = []
        status = 0

        async def receive_copy() -> Message:
            message = await receive()
            if message["type"] == "http.request" and sum(map(len, received)) < self.LIMIT:
                received.append(message.get("body", b""))
            return message

        async def send_copy(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body" and sum(map(len, sent)) < self.LIMIT:
                sent.append(message.get("body", b""))
            await send(message)

        await self.app(scope, receive_copy, send_copy)
        action = ACTIONS.get(getattr(scope.get("endpoint"), "__name__", ""))
        if action is None or not (status < 400 or (action == "login" and status in (401, 403, 429))):
            return
        try:
            row = entry(scope, action, status, b"".join(sent), b"".join(received))
            await asyncio.to_thread(scope["app"].state.store.add_audit, *row)
        except Exception:  # noqa: BLE001 - le journal ne doit jamais faire échouer l'action elle-même
            log.exception("journal d'activité : écriture impossible (%s)", action)
