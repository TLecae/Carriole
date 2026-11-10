"""Préférences de l'utilisateur modifiables depuis l'interface (pour l'instant : la langue)."""

from __future__ import annotations

from typing import Annotated, Any

from etl_engine import i18n, settings
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from etl_api.auth import Identity, server_mode
from etl_api.deps import current_identity

router = APIRouter(tags=["préférences"])


class LanguageIn(BaseModel):
    language: str
    from_installer: bool = False  # langue choisie dans l'installeur : mémorisée pour ne pas être reprise deux fois


def _state(chosen: str = "") -> dict[str, Any]:
    current = i18n.normalize(chosen or None)
    return {
        "language": current if current in i18n.available_languages() else i18n.SOURCE_LANGUAGE,
        "configured": bool(chosen or settings.get("language")),  # faux : jamais choisie
        "languages": [{"code": c, "name": i18n.language_name(c)} for c in i18n.available_languages()],
    }


def _user_language(request: Request, identity: Identity) -> str:
    user = request.app.state.store.get_user(identity.name) if server_mode() else None
    return (user or {}).get("language", "")


@router.get("/preferences")
def get_preferences(request: Request, identity: Annotated[Identity, Depends(current_identity)]) -> dict[str, Any]:
    return _state(_user_language(request, identity))


@router.put("/preferences/language")
def set_language(
    body: LanguageIn, request: Request, identity: Annotated[Identity, Depends(current_identity)]
) -> dict[str, Any]:
    code = i18n.normalize(body.language)
    if code not in i18n.available_languages():
        raise HTTPException(422, f"langue inconnue : {body.language}")
    if server_mode():  # serveur : la langue de chacun, pas celle de la machine
        request.app.state.store.set_user_language(identity.name, code)
        return _state(code)
    settings.set_value("language", code)
    if body.from_installer:
        settings.set_value("installer_language", code)
    return _state()
