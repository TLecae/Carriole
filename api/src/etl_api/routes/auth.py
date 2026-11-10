"""Connexion, déconnexion et identité courante (ADR 0052)."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from etl_engine import i18n
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from etl_api.auth import AuthError, AuthService, AuthUnavailable, Identity, server_mode
from etl_api.auth.providers import LdapProvider
from etl_api.auth.service import session_token
from etl_api.auth.sessions import COOKIE
from etl_api.deps import Admin, current_identity, request_lang

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
    """Pour la page de connexion : mode, fournisseur, et s'il faut un formulaire (le proxy, lui, identifie seul)."""
    service = getattr(request.app.state, "auth", None)
    if service is None:
        return {"mode": "desktop", "provider": None, "password": False}
    return {"mode": "server", "provider": service.config.provider, "password": service.passwords is not None}


@router.get("/auth/me")
def me(request: Request, identity: Annotated[Identity, Depends(current_identity)]) -> dict[str, Any]:
    service = getattr(request.app.state, "auth", None)
    return {
        **identity.as_json(),
        "mode": "server" if server_mode() else "desktop",
        "logout": service is not None and service.passwords is not None,
    }


@router.post("/auth/login")
async def login(body: LoginIn, request: Request, response: Response) -> dict[str, Any]:
    service = _service(request)
    if service.passwords is None:
        raise HTTPException(400, "la connexion passe par le proxy d'authentification")
    keys = (f"user:{body.username.strip().lower()}", f"ip:{request.state.client_ip}")
    wait = service.throttle.wait(*keys)
    if wait > 0:
        raise HTTPException(
            429, "trop d'essais : réessayer dans un instant", headers={"Retry-After": str(int(wait) + 1)}
        )
    try:
        identity = await run_in_threadpool(service.passwords.authenticate, body.username, body.password)
    except AuthError as exc:
        service.throttle.fail(*keys)
        log.info("connexion refusée : %s depuis %s (%s)", body.username.strip()[:100], request.state.client_ip, exc)
        raise HTTPException(401, str(exc)) from None
    except AuthUnavailable as exc:
        raise HTTPException(503, f"connexion impossible : {exc}") from None
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


@router.post("/auth/check")
def check(request: Request, _: Admin) -> dict[str, Any]:
    """*Paramètres* (administrateur) : l'annuaire LDAP répond-il avec les réglages du serveur ?"""
    service = _service(request)
    if not isinstance(service.passwords, LdapProvider):
        raise HTTPException(400, "le fournisseur d'identité n'est pas un annuaire LDAP")
    try:
        result = service.passwords.check()
    except AuthUnavailable as exc:
        result = {"ok": False, "message": str(exc)}
    return {**result, "message": i18n.tr(result["message"], request_lang(request))}


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    service = getattr(request.app.state, "auth", None)
    token = session_token(request.headers)
    if service is not None and token:
        service.sessions.close(token)
    response.delete_cookie(COOKIE, path="/")


@router.get("/users")
def users(request: Request, identity: Annotated[Identity, Depends(current_identity)]) -> dict[str, Any]:
    """Destinataires possibles d'un partage : les utilisateurs déjà connectés et leurs groupes (mode serveur)."""
    if not server_mode():
        return {"users": [], "groups": []}
    people = request.app.state.store.list_users()
    groups = sorted({g for u in people for g in u["groups"]})
    return {"users": [{"name": u["name"], "display": u["display"]} for u in people], "groups": groups}
