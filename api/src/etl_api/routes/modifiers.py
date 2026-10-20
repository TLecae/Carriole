from __future__ import annotations

from typing import Annotated, Any

from etl_engine import components, i18n
from etl_engine.flow import Flow
from etl_engine.registry import Registry
from etl_store import Store
from fastapi import APIRouter, Depends

from etl_api.deps import get_registry, get_store, request_lang

router = APIRouter(tags=["modifiers"])


@router.get("/modifiers")
def list_modifiers(
    registry: Annotated[Registry, Depends(get_registry)],
    store: Annotated[Store, Depends(get_store)],
    lang: Annotated[str, Depends(request_lang)],
) -> dict[str, Any]:
    """Palette : chaque modificateur avec ses ports et le JSON Schema de ses paramètres.

    Les composants (flux réutilisables) y figurent comme des modificateurs : `component: true`, `flow_id` du flux.
    """
    items = [cls.describe(lang) for cls in registry.all()]
    errors = dict(registry.errors)
    latest = store.latest_component_versions()
    for row in store.list_components():
        name = row["component"]
        try:
            # la palette décrit la dernière version publiée (ce que les nœuds suivent), à défaut le flux lui-même
            published = store.get_component_version(name) if name in latest else None
            definition = published["flow"] if published else row["flow"]
            info = components.describe(name, Flow.model_validate(definition))
        except ValueError as exc:  # un composant cassé n'empêche pas la palette
            errors[f"composant {name}"] = str(exc)
            continue
        info["category"] = i18n.localize_category(info["category"], lang)
        items.append({**info, "flow_id": row["id"], "latest_version": latest.get(name)})
    return {"modifiers": items, "errors": {k: i18n.tr(v, lang) for k, v in errors.items()}, "language": lang}
