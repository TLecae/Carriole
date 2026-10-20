"""Fournisseur de composants pour le moteur : les flux de la base qui déclarent `component`."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from etl_store.store import Store


def store_components(store: Store) -> Callable[[str, int | None], Any]:
    """Fournisseur pour `etl_engine.components.install_provider` (renvoie `(Flow, version)` ou None)."""
    from etl_engine.flow import Flow

    def find(name: str, version: int | None) -> tuple[Flow, int | None] | None:
        published = store.get_component_version(name, version)
        if published is not None:
            return Flow.model_validate(published["flow"]), published["version"]
        if version is not None:
            return None  # version demandée inexistante
        row = store.get_component(name)  # aucune version publiée : le flux lui-même (brouillon)
        return (Flow.model_validate(row["flow"]), None) if row else None

    return find
