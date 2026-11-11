"""Comptes du mode serveur, gérés par l'administrateur depuis Carriole (ADR 0058, 0059)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from etl_api.auth import AuthError, Identity
from etl_api.auth.accounts import AccountError, LocalAccounts
from etl_api.deps import Admin, current_identity

router = APIRouter(tags=["comptes"])


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    display: str = Field("", max_length=200)
    groups: list[str] = []
    password: str = Field(max_length=1000)


class AccountPatch(BaseModel):
    display: str | None = Field(None, max_length=200)
    groups: list[str] | None = None
    password: str | None = Field(None, max_length=1000)


class PasswordIn(BaseModel):
    current: str = Field(max_length=1000)
    new: str = Field(max_length=1000)


def _accounts(request: Request) -> LocalAccounts:
    service = getattr(request.app.state, "auth", None)
    if service is None:
        raise HTTPException(404, "pas de comptes sur le poste de bureau")
    return service.accounts


@router.get("/accounts")
def list_accounts(request: Request, _: Admin) -> list[dict[str, Any]]:
    return _accounts(request).accounts()


@router.post("/accounts", status_code=201)
async def create_account(body: AccountIn, request: Request, _: Admin) -> dict[str, Any]:
    accounts = _accounts(request)
    try:
        return await run_in_threadpool(accounts.create, body.name, body.password, body.display, body.groups)
    except AccountError as exc:
        raise HTTPException(409 if "existe déjà" in str(exc) else 422, str(exc)) from None


@router.patch("/accounts/{name}")
async def update_account(name: str, body: AccountPatch, request: Request, _: Admin) -> dict[str, Any]:
    """Nom affiché, groupes, nouveau mot de passe. Un changement de groupes ou de mot de passe ferme les sessions du
    compte : il se reconnecte avec ses nouveaux droits."""
    accounts = _accounts(request)
    try:
        updated = await run_in_threadpool(accounts.update, name, body.display, body.groups, body.password)
    except KeyError:
        raise HTTPException(404, "compte introuvable") from None
    except AccountError as exc:
        raise HTTPException(422, str(exc)) from None
    if body.groups is not None or body.password is not None:
        request.app.state.auth.sessions.close_user(updated["name"])
    return updated


@router.delete("/accounts/{name}", status_code=204)
def delete_account(name: str, request: Request, identity: Admin) -> None:
    accounts = _accounts(request)
    if (accounts.find(name) or "").lower() == identity.name.lower():
        raise HTTPException(409, "impossible de supprimer son propre compte")
    try:
        removed = accounts.delete(name)
    except KeyError:
        raise HTTPException(404, "compte introuvable") from None
    request.app.state.auth.sessions.close_user(removed)


@router.put("/auth/password", status_code=204)
async def change_password(
    body: PasswordIn, request: Request, identity: Annotated[Identity, Depends(current_identity)]
) -> None:
    """Un utilisateur d'un compte local change son mot de passe (l'actuel est redemandé)."""
    accounts = _accounts(request)
    name = accounts.find(identity.name)
    if name is None:
        raise HTTPException(404, "compte introuvable")
    try:
        await run_in_threadpool(accounts.authenticate, name, body.current)
    except AuthError:
        raise HTTPException(403, "mot de passe actuel incorrect") from None
    try:
        await run_in_threadpool(accounts.update, name, None, None, body.new)
    except AccountError as exc:
        raise HTTPException(422, str(exc)) from None
