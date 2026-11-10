"""Dépendances FastAPI : stockage, registre des modificateurs, utilisateur courant."""

from __future__ import annotations

from typing import Annotated

from etl_engine import i18n
from etl_engine.registry import Registry
from etl_store import Settings, Store
from fastapi import Depends, HTTPException, Request

from etl_api.auth import Identity, server_mode


def get_store(request: Request) -> Store:
    return request.app.state.store


def get_registry(request: Request) -> Registry:
    return request.app.state.registry


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def current_identity(request: Request) -> Identity:
    """Qui fait la requête. Mode serveur : l'identité posée par `ServerGuard` (session ou proxy de confiance), jamais un
    en-tête du navigateur. Poste de bureau : un seul utilisateur, administrateur de son poste (le nom vient de
    *Paramètres*, sinon du compte du système)."""
    identity = getattr(request.state, "identity", None)
    if identity is not None:
        return identity
    if server_mode():
        raise HTTPException(401, "authentification requise")
    name = (request.headers.get("x-etl-user") or "").strip()[:100] or request.app.state.settings.default_user
    return Identity(name=name, display=name, admin=True)


def current_user(request: Request) -> str:
    return current_identity(request).name


def require_admin(identity: Annotated[Identity, Depends(current_identity)]) -> Identity:
    """Réglages de la machine, plugins, journal : réservés aux administrateurs (`ETL_ADMINS`) en mode serveur."""
    if not identity.admin:
        raise HTTPException(403, "réservé aux administrateurs de Carriole")
    return identity


Admin = Annotated[Identity, Depends(require_admin)]


def request_lang(request: Request) -> str:
    """Langue demandée : paramètre `lang`, sinon en-tête `Accept-Language`, sinon réglage de l'utilisateur."""
    wanted = request.query_params.get("lang") or request.headers.get("accept-language")
    lang = i18n.normalize(wanted)
    return lang if lang in i18n.available_languages() else i18n.SOURCE_LANGUAGE
