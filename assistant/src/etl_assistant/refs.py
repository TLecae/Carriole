"""Références `@` du message : fichiers, ports de run et nœuds sont résolus en résumés que le modèle peut lire."""

from __future__ import annotations

import json
from typing import Any

from etl_assistant.tools import Toolbox, ToolError

MAX_REFERENCES = 8


def resolve(references: list[dict[str, Any]], toolbox: Toolbox) -> str:
    """Bloc de texte à joindre au message de l'utilisateur ; vide s'il n'y a rien. Une référence illisible est signalée."""
    parts = []
    for ref in references[:MAX_REFERENCES]:
        kind = ref.get("kind")
        try:
            if kind == "file":
                path = str(ref["path"])
                parts.append((f"@{path}", toolbox.call_read("inspect_file", {"path": path})))
            elif kind == "port":
                node, port = str(ref["node"]), str(ref.get("port", "output"))
                args: dict[str, Any] = {"node": node, "port": port}
                if ref.get("run"):
                    args["run_id"] = str(ref["run"])
                parts.append((f"@{node}.{port}", toolbox.call_read("get_port_sample", args)))
            elif kind == "node":
                node = next((n for n in toolbox.flow.get("nodes", []) if n["id"] == ref.get("id")), None)
                if node is None:
                    raise ToolError(f"nœud introuvable : {ref.get('id')}")
                parts.append((f"@{node['id']}", json.dumps(node, ensure_ascii=False, default=str)[:3000]))
            else:
                raise ToolError(f"type de référence inconnu : {kind}")
        except (ToolError, KeyError) as exc:
            label = ref.get("path") or ref.get("node") or ref.get("id") or kind
            parts.append((f"@{label}", f"[illisible : {exc}]"))
    if not parts:
        return ""
    return "References attached by the user:\n" + "\n".join(f"{label}: {text}" for label, text in parts)
