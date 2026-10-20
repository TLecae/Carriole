"""Préférences de l'utilisateur modifiables depuis l'interface (pour l'instant : la langue)."""

from __future__ import annotations

from typing import Any

from etl_engine import i18n, settings
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["préférences"])


class LanguageIn(BaseModel):
    language: str
    from_installer: bool = False  # langue choisie dans l'installeur : mémorisée pour ne pas être reprise deux fois


def _state() -> dict[str, Any]:
    current = i18n.normalize(None)
    return {
        "language": current if current in i18n.available_languages() else i18n.SOURCE_LANGUAGE,
        "configured": bool(settings.get("language")),  # faux : jamais choisie
        "languages": [{"code": c, "name": i18n.language_name(c)} for c in i18n.available_languages()],
    }


@router.get("/preferences")
def get_preferences() -> dict[str, Any]:
    return _state()


@router.put("/preferences/language")
def set_language(body: LanguageIn) -> dict[str, Any]:
    code = i18n.normalize(body.language)
    if code not in i18n.available_languages():
        raise HTTPException(422, f"langue inconnue : {body.language}")
    settings.set_value("language", code)
    if body.from_installer:
        settings.set_value("installer_language", code)
    return _state()
