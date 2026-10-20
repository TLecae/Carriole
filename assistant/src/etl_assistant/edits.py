"""Modifications de flux proposées par l'assistant : opérations validées, appliquées à une copie, jamais au flux ouvert.

L'interface reçoit le flux résultant (`flow`) et la liste des opérations : elle montre ce qui change et ne l'applique
que sur un clic de l'utilisateur (avec l'annuler / rétablir de l'éditeur).
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable
from typing import Any

from etl_engine.analysis import analyze
from etl_engine.flow import Flow
from etl_engine.registry import Registry
from pydantic import ValidationError

ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]{0,59}$")
MAX_OPERATIONS = 60


class EditError(Exception):
    """Une opération est invalide : le message est renvoyé au modèle pour qu'il corrige."""


def _next_id(prefix: str, taken: Iterable[str]) -> str:
    used = set(taken)
    i = 1
    while f"{prefix}_{i}" in used:
        i += 1
    return f"{prefix}_{i}"


def _node(flow: dict[str, Any], node_id: Any) -> dict[str, Any]:
    for n in flow["nodes"]:
        if n["id"] == node_id:
            return n
    known = ", ".join(n["id"] for n in flow["nodes"]) or "aucun"
    raise EditError(f"nœud introuvable : {node_id} (nœuds du flux : {known})")


def _endpoint(value: Any, what: str) -> list[str]:
    if isinstance(value, dict):
        value = [value.get("node"), value.get("port")]
    if not (isinstance(value, (list, tuple)) and len(value) == 2 and all(isinstance(v, str) and v for v in value)):
        raise EditError(f'{what} : donner [nœud, port], par exemple ["Reader_1", "output"]')
    return [value[0], value[1]]


def apply_operations(flow: dict[str, Any], operations: list[dict[str, Any]], registry: Registry) -> dict[str, Any]:
    """Flux obtenu en appliquant `operations` à une copie de `flow`. Lève `EditError` au premier problème."""
    if not operations:
        raise EditError("aucune opération")
    if len(operations) > MAX_OPERATIONS:
        raise EditError(f"trop d'opérations ({len(operations)}) : en proposer {MAX_OPERATIONS} au plus à la fois")
    out = copy.deepcopy(flow)
    out.setdefault("nodes", [])
    out.setdefault("links", [])
    added = 0
    for number, op in enumerate(operations, 1):
        if not isinstance(op, dict):
            raise EditError(f"opération {number} : un objet est attendu")
        kind = op.get("op")
        try:
            if kind == "add_node":
                type_name = op.get("type")
                if not isinstance(type_name, str) or not type_name:
                    raise EditError("add_node : « type » est obligatoire")
                if not type_name.startswith("component:") and type_name not in registry:
                    raise EditError(f"modifier inconnu : {type_name} (voir list_modifiers)")
                node_id = op.get("id") or _next_id(
                    re.sub(r"[^A-Za-z0-9_.\-]+", "_", type_name.split(":")[-1]), (n["id"] for n in out["nodes"])
                )
                if not ID_RE.match(str(node_id)):
                    raise EditError(f"identifiant de nœud invalide : {node_id}")
                if any(n["id"] == node_id for n in out["nodes"]):
                    raise EditError(f"identifiant déjà pris : {node_id}")
                position = op.get("position")
                if not (isinstance(position, (list, tuple)) and len(position) == 2):
                    right = max((n.get("position", [0, 0])[0] for n in out["nodes"]), default=-240.0)
                    position = [right + 240.0 if out["nodes"] else 80.0, 80.0 + 130.0 * added]
                params = op.get("params") or {}
                if not isinstance(params, dict):
                    raise EditError("add_node : « params » doit être un objet")
                out["nodes"].append(
                    {
                        "id": node_id,
                        "type": type_name,
                        "label": op.get("label") or "",
                        "params": params,
                        "position": list(position),
                        "enabled": True,
                    }
                )
                added += 1
            elif kind == "set_params":
                node = _node(out, op.get("node"))
                params = op.get("params")
                if not isinstance(params, dict):
                    raise EditError("set_params : « params » doit être un objet")
                node["params"] = {**node.get("params", {}), **params}
            elif kind == "set_label":
                _node(out, op.get("node"))["label"] = str(op.get("label", ""))
            elif kind == "set_enabled":
                _node(out, op.get("node"))["enabled"] = bool(op.get("enabled", True))
            elif kind == "remove_node":
                node = _node(out, op.get("node"))
                out["nodes"].remove(node)
                out["links"] = [link for link in out["links"] if node["id"] not in (link["from"][0], link["to"][0])]
            elif kind == "connect":
                src, dst = _endpoint(op.get("from"), "from"), _endpoint(op.get("to"), "to")
                _node(out, src[0]), _node(out, dst[0])
                if any(link["from"] == src and link["to"] == dst for link in out["links"]):
                    raise EditError(f"lien déjà présent : {src[0]}.{src[1]} → {dst[0]}.{dst[1]}")
                out["links"].append(
                    {
                        "id": _next_id("link", (link["id"] for link in out["links"])),
                        "from": src,
                        "to": dst,
                        "enabled": True,
                    }
                )
            elif kind == "disconnect":
                src, dst = _endpoint(op.get("from"), "from"), _endpoint(op.get("to"), "to")
                kept = [link for link in out["links"] if not (link["from"] == src and link["to"] == dst)]
                if len(kept) == len(out["links"]):
                    raise EditError(f"lien introuvable : {src[0]}.{src[1]} → {dst[0]}.{dst[1]}")
                out["links"] = kept
            elif kind == "set_variable":
                name = op.get("name")
                if not isinstance(name, str) or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
                    raise EditError("set_variable : nom de variable invalide")
                out.setdefault("variables", {})[name] = str(op.get("value", ""))
            else:
                raise EditError(
                    f"opération inconnue : {kind!r} (add_node, set_params, set_label, set_enabled, remove_node, "
                    "connect, disconnect, set_variable)"
                )
        except EditError as exc:
            raise EditError(f"opération {number} ({kind}) : {exc}") from None
    return out


def with_defaults(flow: dict[str, Any]) -> dict[str, Any]:
    """Complète un flux écrit à la main (par le modèle) avec les clés que l'éditeur attend."""
    out = dict(flow)
    for key, default in (
        ("published_params", []),
        ("annotations", []),
        ("bookmarks", []),
        ("variables", {}),
        ("subflows", {}),
    ):
        out.setdefault(key, default)
    out.setdefault("schema_version", 1)
    out["nodes"] = [
        {"label": "", "params": {}, "position": [80.0 + 240.0 * i, 80.0], "enabled": True, **n}
        for i, n in enumerate(flow.get("nodes", []))
    ]
    out["links"] = [{"enabled": True, **link} for link in flow.get("links", [])]
    return out


def validate(flow: dict[str, Any], registry: Registry, lang: str = "fr") -> dict[str, Any]:
    """Flux valide pour le modèle, puis avertissements et erreurs de l'analyse (sans lire de données)."""
    try:
        model = Flow.model_validate(flow)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise EditError(f"flux invalide : {'.'.join(str(p) for p in first['loc'])} : {first['msg']}") from None
    return analyze(model, registry, {}, lang)


def describe_operations(operations: list[dict[str, Any]]) -> list[str]:
    """Résumé lisible (français : texte source de l'interface) de chaque opération."""
    lines = []
    for op in operations:
        kind = op.get("op")
        if kind == "add_node":
            lines.append(f"Ajouter {op.get('type')}" + (f" ({op['id']})" if op.get("id") else ""))
        elif kind == "set_params":
            lines.append(f"Régler {op.get('node')} : {', '.join(map(str, (op.get('params') or {}).keys()))}")
        elif kind == "remove_node":
            lines.append(f"Supprimer {op.get('node')}")
        elif kind in ("connect", "disconnect"):
            a, b = op.get("from"), op.get("to")
            verb = "Relier" if kind == "connect" else "Délier"
            lines.append(f"{verb} {a[0]}.{a[1]} → {b[0]}.{b[1]}" if a and b else verb)
        elif kind == "set_label":
            lines.append(f"Renommer {op.get('node')} en « {op.get('label')} »")
        elif kind == "set_enabled":
            lines.append(f"{'Activer' if op.get('enabled', True) else 'Désactiver'} {op.get('node')}")
        elif kind == "set_variable":
            lines.append(f"Variable {op.get('name')}")
        else:
            lines.append(str(kind))
    return lines
