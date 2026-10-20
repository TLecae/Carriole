"""Lettres de lecteur réseau (`Z:`) dans un flux.

Une tâche planifiée qui s'exécute hors de la session de l'utilisateur ne voit pas toujours ses lecteurs réseau mappés :
le chemin réseau (`//serveur/partage/...`) est plus sûr. Windows seulement ; ailleurs, aucun avertissement.
"""

from __future__ import annotations

import os
import re
from typing import Any

_DRIVE = re.compile(r"^([A-Za-z]):[\\/]")
DRIVE_REMOTE = 4  # GetDriveTypeW


def _is_network_drive(letter: str) -> bool:
    import ctypes

    return ctypes.windll.kernel32.GetDriveTypeW(f"{letter.upper()}:\\") == DRIVE_REMOTE


def is_network_path(path: str | os.PathLike[str]) -> bool:
    """Vrai si `path` est sur un partage réseau : chemin UNC (`//serveur/partage`) ou lecteur réseau mappé (`Z:`)."""
    if os.name != "nt":
        return False
    text = os.fspath(path).replace(chr(92), "/")
    if text.startswith("//") and not text.startswith("//?/") and not text.startswith("//./"):
        return True
    match = _DRIVE.match(text)
    return bool(match and _is_network_drive(match[1]))


def network_drive_paths(value: Any) -> list[str]:
    """Chemins du contenu d'un flux qui commencent par la lettre d'un lecteur réseau."""
    if os.name != "nt":
        return []
    found: dict[str, None] = {}

    def walk(item: Any) -> None:
        if isinstance(item, str):
            match = _DRIVE.match(item)
            if match and _is_network_drive(match[1]):
                found[item] = None
        elif isinstance(item, list):
            for x in item:
                walk(x)
        elif isinstance(item, dict):
            for x in item.values():
                walk(x)

    walk(value)
    return list(found)


def warnings_for(flow_content: dict[str, Any]) -> list[str]:
    """Avertissements à montrer sur une planification de ce flux."""
    return [
        f"{p} : lecteur réseau, que la tâche planifiée ne voit pas toujours ; préférer le chemin //serveur/partage/…"
        for p in network_drive_paths(flow_content.get("nodes", []))
    ]
