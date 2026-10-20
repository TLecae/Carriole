"""Dépendances FastAPI : stockage, registre des modificateurs, utilisateur courant."""

from __future__ import annotations

from etl_engine import i18n
from etl_engine.registry import Registry
from etl_store import Settings, Store
from fastapi import Request


def get_store(request: Request) -> Store:
    return request.app.state.store


def get_registry(request: Request) -> Registry:
    return request.app.state.registry


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def current_user(request: Request) -> str:
    """Identité légère : en-tête posé par le proxy d'authentification ou par l'interface (pas de droits fins)."""
    name = (request.headers.get("x-etl-user") or "").strip()
    return name[:100] or request.app.state.settings.default_user


def request_lang(request: Request) -> str:
    """Langue demandée : paramètre `lang`, sinon en-tête `Accept-Language`, sinon réglage de l'utilisateur."""
    wanted = request.query_params.get("lang") or request.headers.get("accept-language")
    lang = i18n.normalize(wanted)
    return lang if lang in i18n.available_languages() else i18n.SOURCE_LANGUAGE
