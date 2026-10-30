"""Réglages de l'application modifiables depuis l'interface (ce que fait aussi `etl config`)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, Any

from etl_engine import __version__, drives, settings
from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

router = APIRouter(tags=["réglages"])

DEFAULT_WORKERS = 3
RESTART_KEYS = frozenset({"workers", "memory_budget_mb", "proxy", "cache_dir"})  # lus au démarrage du moteur
_TILE_HOST = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d{1,5})?$")
_VARIABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SettingsIn(BaseModel):
    """Modification partielle : seuls les champs envoyés changent ; `null` rétablit la valeur par défaut."""

    model_config = ConfigDict(extra="forbid")

    workers: int | None = Field(None, ge=1, le=32)
    memory_budget_mb: int | None = Field(None, ge=256, le=4_194_304)
    cache_dir: str | None = None  # dossier du cache des runs (et du débordement des calculs) ; vide : celui par défaut
    proxy: str | None = None
    component_dirs: list[str] | None = None
    tile_hosts: list[str] | None = None
    variables: dict[str, str] | None = None
    keep_running: bool | None = None  # poste de bureau : fermer la fenêtre laisse Carriole en arrière-plan

    @field_validator("proxy")
    @classmethod
    def _proxy(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if value and not re.fullmatch(r"https?://\S+", value):
            raise ValueError("adresse du proxy invalide : http://hote:port attendu")
        return value or None

    @field_validator("cache_dir")
    @classmethod
    def _cache_dir(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if not value:
            return None
        if any(ord(c) < 32 for c in value) or not Path(value).is_absolute():
            raise ValueError("chemin complet du dossier attendu (ex. D:/carriole/cache)")
        if drives.is_network_path(value):
            raise ValueError("le dossier du cache doit être sur un disque de ce poste, pas sur un partage réseau")
        return value

    @field_validator("component_dirs")
    @classmethod
    def _dirs(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [v.strip() for v in value if v.strip()]
        if any(any(ord(c) < 32 for c in v) for v in cleaned):
            raise ValueError("chemin de dossier invalide")
        return list(dict.fromkeys(cleaned)) or None

    @field_validator("tile_hosts")
    @classmethod
    def _hosts(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [v.strip().rstrip("/") for v in value if v.strip()]
        for host in cleaned:  # ces adresses entrent dans la politique de sécurité du navigateur : forme stricte
            if not _TILE_HOST.match(host):
                raise ValueError(f"serveur de tuiles invalide : {host} (https://hote ou https://hote:port attendu)")
        return list(dict.fromkeys(cleaned)) or None

    @field_validator("variables")
    @classmethod
    def _variables(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        for name, text in value.items():
            if not _VARIABLE.match(name):
                raise ValueError(
                    f"nom de variable invalide : {name} (lettres, chiffres et _, sans commencer par un chiffre)"
                )
            if len(text) > 4000:
                raise ValueError(f"valeur trop longue pour la variable {name}")
        return value or None


def _state() -> dict[str, Any]:
    dirs = settings.app_dirs()
    component_dirs = [str(d) for d in settings.get("component_dirs", []) or []]
    return {
        "version": __version__,
        "values": {
            "workers": settings.get("workers") if settings.get("workers") is not None else None,
            "memory_budget_mb": settings.get("memory_budget_mb"),
            "cache_dir": settings.get("cache_dir") or "",
            "proxy": settings.get("proxy") or "",
            "component_dirs": component_dirs,
            "tile_hosts": [str(h) for h in settings.get("tile_hosts", []) or []],
            "variables": {str(k): str(v) for k, v in (settings.get("variables", {}) or {}).items()},
            "keep_running": settings.get("keep_running"),
        },
        "defaults": {"workers": DEFAULT_WORKERS},
        "component_dirs_found": {d: Path(d).is_dir() for d in component_dirs},
        "paths": {
            "data": str(dirs.data),
            "settings": str(dirs.settings_file),
            "logs": str(dirs.logs),
            "plugins": str(dirs.plugins),
            "cache": os.environ.get("ETL_CACHE_DIR") or str(dirs.cache),
        },
    }


@router.get("/settings")
def get_settings() -> dict[str, Any]:
    return _state()


@router.patch("/settings")
def patch_settings(raw: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    try:
        body = SettingsIn.model_validate(raw)
    except ValidationError as exc:  # message court, traduit comme les autres erreurs de l'API
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first["loc"])
        message = str(first["msg"]).removeprefix("Value error, ")
        raise HTTPException(422, f"{field} : {message}" if field else message) from None
    changed = []
    current = settings.load()
    for key in body.model_fields_set:
        value = getattr(body, key)
        if value is None:
            if settings.unset(key):
                changed.append(key)
        elif current.get(key) != value:
            settings.set_value(key, value)
            changed.append(key)
    return {**_state(), "restart_required": sorted(k for k in changed if k in RESTART_KEYS)}
