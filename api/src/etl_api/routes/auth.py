"""Connexion, déconnexion et identité courante (comptes locaux, ADR 0052, 0059)."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from etl_api.auth import AuthError, AuthService, Identity, server_mode
from etl_api.auth.service import session_token
from etl_api.auth.sessions import COOKIE
from etl_api.deps import Admin, current_identity

router = APIRouter(tags=["authentification"])
log = logging.getLogger("etl.auth")


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(max_length=1000)


def _service(request: Request) -> AuthService:
    service = getattr(request.app.state, "auth", None)
    if service is None:
        raise HTTPException(404, "pas de connexion sur le poste de bureau")
    return service


@router.get("/auth/info")
def info(request: Request) -> dict[str, Any]:
    """Pour la page de connexion : serveur (identifiant et mot de passe) ou poste de bureau (pas de connexion)."""
    service = getattr(request.app.state, "auth", None)
    return {"mode": "desktop" if service is None else "server", "password": service is not None}


@router.get("/auth/me")
def me(request: Request, identity: Annotated[Identity, Depends(current_identity)]) -> dict[str, Any]:
    service = getattr(request.app.state, "auth", None)
    return {
        **identity.as_json(),
        "mode": "server" if server_mode() else "desktop",
        # serveur : une session à fermer, des comptes gérés par l'administrateur, un mot de passe à changer
        "logout": service is not None,
        "accounts": service is not None,
        "local": service is not None and service.accounts.find(identity.name) is not None,
    }


@router.post("/auth/login")
async def login(body: LoginIn, request: Request, response: Response) -> dict[str, Any]:
    service = _service(request)
    keys = (f"user:{body.username.strip().lower()}", f"ip:{request.state.client_ip}")
    wait = service.throttle.wait(*keys)
    if wait > 0:
        raise HTTPException(
            429, "trop d'essais : réessayer dans un instant", headers={"Retry-After": str(int(wait) + 1)}
        )
    try:
        identity = await run_in_threadpool(service.accounts.authenticate, body.username, body.password)
    except AuthError as exc:
        service.throttle.fail(*keys)
        log.info("connexion refusée : %s depuis %s (%s)", body.username.strip()[:100], request.state.client_ip, exc)
        raise HTTPException(401, str(exc)) from None
    service.throttle.succeed(keys[0])
    token = await run_in_threadpool(service.sessions.open, identity)
    log.info("connexion : %s depuis %s", identity.name, request.state.client_ip)
    response.set_cookie(
        COOKIE,
        token,
        max_age=int(service.sessions.seconds),
        httponly=True,
        samesite="strict",
        secure=bool(request.state.secure),
        path="/",
    )
    return service.with_role(identity).as_json()


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    service = getattr(request.app.state, "auth", None)
    token = session_token(request.headers)
    if service is not None and token:
        service.sessions.close(token)
    response.delete_cookie(COOKIE, path="/")


@router.get("/users")
def users(request: Request, identity: Annotated[Identity, Depends(current_identity)]) -> dict[str, Any]:
    """Destinataires possibles d'un partage : les comptes et leurs groupes (mode serveur)."""
    service = getattr(request.app.state, "auth", None)
    if service is None:
        return {"users": [], "groups": []}
    people = service.accounts.accounts()
    groups = sorted({g for u in people for g in u["groups"]})
    return {"users": [{"name": u["name"], "display": u["display"]} for u in people], "groups": groups}


@router.get("/audit")
def audit_log(
    request: Request,
    _: Admin,
    user: str | None = None,
    action: str | None = None,
    before: int | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Journal d'activité (administrateurs, mode serveur) : du plus récent au plus ancien, par pages."""
    if not server_mode():
        return []
    return request.app.state.store.list_audit(user or None, action or None, before, min(max(limit, 1), 1000))
