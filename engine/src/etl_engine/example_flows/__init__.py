"""Flux d'exemple livrés avec Carriole, proposés sur la page d'accueil.

Ils lisent des données publiques par https (API, catalogues) : on peut les lancer dès l'installation, sans fichier ni
base. Leurs écritures sont désactivées, des annotations expliquent chaque étape.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

from etl_engine.flow import Flow


def _files() -> list[Any]:
    folder = resources.files(__name__)
    return sorted((f for f in folder.iterdir() if f.name.endswith(".json")), key=lambda f: f.name)


def list_examples() -> list[dict[str, Any]]:
    """Résumé de chaque exemple : identifiant (nom du fichier), nom, description, nombre de nœuds."""
    out = []
    for f in _files():
        doc = json.loads(f.read_text(encoding="utf-8"))
        out.append(
            {
                "id": f.name.removesuffix(".json"),
                "name": doc.get("name", f.name),
                "description": doc.get("description", ""),
                "node_count": len(doc.get("nodes", [])),
            }
        )
    return out


def load_example(example_id: str) -> dict[str, Any] | None:
    """Le flux d'un exemple, validé comme un flux ouvert depuis un fichier (anciens noms de modificateurs compris)."""
    for f in _files():
        if f.name.removesuffix(".json") == example_id:
            flow = Flow.model_validate(json.loads(f.read_text(encoding="utf-8")))
            return flow.model_dump(mode="json", by_alias=True)
    return None
