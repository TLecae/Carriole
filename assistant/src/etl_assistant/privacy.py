"""Confidentialité : ce que l'assistant peut transmettre au fournisseur de modèle.

Les données foncières sont sous convention : par défaut, un fournisseur distant ne reçoit que des **schémas**
(noms et types de champs, nombres, SCR) et jamais de valeurs. Un modèle local (Ollama) peut recevoir des lignes.
Ce qui part dans tous les cas : le texte de l'utilisateur, le flux ouvert (nœuds, paramètres, chemins) et les messages
des journaux d'exécution (tronqués).
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

MODES = ("schema", "sample")
MAX_SAMPLE_ROWS = 20
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


@dataclass(frozen=True)
class Privacy:
    mode: str = "schema"  # "schema" : aucune valeur ; "sample" : schéma + quelques lignes
    sample_rows: int = 5

    @property
    def share_rows(self) -> bool:
        return self.mode == "sample"

    def clamp_rows(self, wanted: int) -> int:
        return max(1, min(int(wanted), self.sample_rows, MAX_SAMPLE_ROWS))


def is_local(base_url: str | None) -> bool:
    return bool(base_url) and (urlparse(base_url).hostname or "") in LOCAL_HOSTS


def default_mode(kind: str, base_url: str | None) -> str:
    """« schéma seulement » pour un fournisseur distant, « échantillon » pour un modèle sur le poste."""
    return "sample" if kind == "openai" and is_local(base_url) else "schema"


def from_config(config: dict, kind: str, base_url: str | None) -> Privacy:
    mode = config.get("privacy") or default_mode(kind, base_url)
    if mode not in MODES:
        mode = "schema"
    rows = config.get("sample_rows", 5)
    return Privacy(mode, max(1, min(int(rows) if isinstance(rows, int) else 5, MAX_SAMPLE_ROWS)))
