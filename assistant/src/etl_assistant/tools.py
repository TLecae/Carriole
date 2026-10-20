"""Outils de l'assistant.

- **Lecture** : exécutés tout de suite, sans confirmation (catalogue, fichiers, flux ouvert, runs, échantillons).
- **Action** : jamais exécutés par le serveur. Ils produisent une *proposition* que l'interface montre ; l'utilisateur
  la valide d'un clic (modifier le flux, lancer, installer un modificateur, créer un composant).

Ce que le modèle voit dépend du mode de confidentialité (`privacy.py`) : en mode « schéma », aucune valeur de donnée.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from etl_engine.registry import Registry

from etl_assistant import edits
from etl_assistant.privacy import Privacy

MAX_RESULT_CHARS = 9000


class ToolError(Exception):
    """Erreur d'outil : renvoyée au modèle comme résultat d'erreur (il peut corriger et réessayer)."""


class Backend(Protocol):
    """Ce que l'API fournit à l'assistant (fichiers, runs, données en cache)."""

    registry: Registry

    def describe_file(self, path: str, layer: str | None, count: bool) -> dict[str, Any]: ...
    def list_dir(self, path: str) -> dict[str, Any]: ...
    def run_info(self, run_id: str) -> dict[str, Any]: ...
    def run_logs(self, run_id: str, limit: int) -> list[dict[str, Any]]: ...
    def port_info(self, run_id: str, node: str, port: str) -> dict[str, Any]: ...
    def port_rows(self, run_id: str, node: str, port: str, limit: int) -> list[dict[str, Any]]: ...


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


_OPERATION = {
    "type": "object",
    "description": (
        "One edit. op is one of: add_node {type, id?, label?, params?, position?}, set_params {node, params} (merged "
        "into the existing params), set_label {node, label}, set_enabled {node, enabled}, remove_node {node}, "
        "connect {from: [node, port], to: [node, port]}, disconnect {from, to}, set_variable {name, value}."
    ),
    "properties": {"op": {"type": "string"}},
    "required": ["op"],
    "additionalProperties": True,
}

READ_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_modifiers",
        "description": "List the available modifiers (name, label, category, short description). Optional category filter.",
        "input_schema": _schema({"category": {"type": "string"}}),
    },
    {
        "name": "describe_modifier",
        "description": "Full description of one modifier: ports, help text and the JSON Schema of its parameters.",
        "input_schema": _schema({"name": {"type": "string"}}, ["name"]),
    },
    {
        "name": "inspect_file",
        "description": "Describe a data file (vector, CSV, Excel, Parquet, raster): format, layers, fields and types, CRS, row count.",
        "input_schema": _schema(
            {"path": {"type": "string"}, "layer": {"type": "string"}, "count": {"type": "boolean"}}, ["path"]
        ),
    },
    {
        "name": "list_dir",
        "description": "List a folder (files and sub-folders). Without a path: the allowed root folders.",
        "input_schema": _schema({"path": {"type": "string"}}),
    },
    {
        "name": "get_flow",
        "description": "The flow currently open in the editor (nodes, params, links, variables).",
        "input_schema": _schema({}),
    },
    {
        "name": "analyze_flow",
        "description": "Static analysis of the open flow: errors and warnings per node, and the schema (fields) at every port.",
        "input_schema": _schema({}),
    },
    {
        "name": "get_run_status",
        "description": "Status of a run: state, error, per-node status and row counts. Default: the latest run of the open flow.",
        "input_schema": _schema({"run_id": {"type": "string"}}),
    },
    {
        "name": "get_run_logs",
        "description": "Log messages of a run (errors and warnings first). Default: the latest run.",
        "input_schema": _schema({"run_id": {"type": "string"}, "limit": {"type": "integer"}}),
    },
    {
        "name": "get_port_sample",
        "description": (
            "Schema and row count of a port of a finished run, plus a few rows when the user allowed it. "
            "Default run: the latest."
        ),
        "input_schema": _schema(
            {
                "node": {"type": "string"},
                "port": {"type": "string"},
                "run_id": {"type": "string"},
                "rows": {"type": "integer"},
            },
            ["node"],
        ),
    },
]

ACTION_TOOLS: list[dict[str, Any]] = [
    {
        "name": "propose_flow_edits",
        "description": (
            "Propose edits to the open flow (add nodes, set params, connect…). The user sees a diff and applies it with "
            "one click; nothing changes until then. Use ids that exist (see get_flow) and the real port names "
            "(describe_modifier). Fix the errors returned and call again."
        ),
        "input_schema": _schema(
            {"operations": {"type": "array", "items": _OPERATION}, "explanation": {"type": "string"}}, ["operations"]
        ),
    },
    {
        "name": "run_flow",
        "description": "Ask to run the open flow (optionally only up to one node). The user confirms with a click.",
        "input_schema": _schema({"until_node": {"type": "string"}}),
    },
    {
        "name": "write_modifier",
        "description": (
            "Propose a new modifier as a Python plugin (a class deriving from etl_engine.sdk.Modifier) with a "
            "pytest test. In the test, import the plugin with `from plugin_<snake_case_name> import <Name>` (for example "
            "DoubleValue -> plugin_double_value) and use etl_engine.testing.run_modifier. The user reviews the code, then "
            "the test runs in an isolated process before installation."
        ),
        "input_schema": _schema(
            {
                "name": {"type": "string", "description": "Class name, CamelCase"},
                "code": {"type": "string"},
                "test_code": {"type": "string"},
                "explanation": {"type": "string"},
            },
            ["name", "code", "test_code"],
        ),
    },
    {
        "name": "create_component",
        "description": (
            "Propose a reusable component: a complete flow with ComponentInput / ComponentOutput nodes. "
            "It opens as a new unsaved flow."
        ),
        "input_schema": _schema(
            {
                "name": {"type": "string"},
                "label": {"type": "string"},
                "category": {"type": "string"},
                "description": {"type": "string"},
                "nodes": {"type": "array", "items": {"type": "object"}},
                "links": {"type": "array", "items": {"type": "object"}},
            },
            ["name", "nodes", "links"],
        ),
    },
]

TOOLS = READ_TOOLS + ACTION_TOOLS
ACTION_NAMES = {t["name"] for t in ACTION_TOOLS}
NAME_RE = re.compile(r"^[A-Z][A-Za-z0-9]{2,40}$")


@dataclass
class Toolbox:
    backend: Backend
    flow: dict[str, Any] = field(default_factory=dict)  # le flux ouvert dans l'éditeur (envoyé à chaque message)
    run_id: str | None = None  # dernier run du flux ouvert
    privacy: Privacy = field(default_factory=Privacy)
    lang: str = "fr"

    @property
    def registry(self) -> Registry:
        return self.backend.registry

    # ------------------------------------------------------------------ appel
    def call_read(self, name: str, args: dict[str, Any]) -> str:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None or name in ACTION_NAMES:
            raise ToolError(f"outil inconnu : {name}")
        try:
            result = handler(**args)
        except TypeError as exc:
            raise ToolError(f"arguments invalides pour {name} : {exc}") from None
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + f"\n… (résultat tronqué à {MAX_RESULT_CHARS} caractères)"
        return text

    # ------------------------------------------------------------------ lecture
    def _tool_list_modifiers(self, category: str | None = None) -> list[dict[str, Any]]:
        rows = []
        for cls in self.registry.all():
            if cls.hidden:
                continue
            info = cls.describe(self.lang)
            if category and category.lower() not in (info["category"].lower(), info["category_id"].lower()):
                continue
            rows.append({k: info[k] for k in ("name", "label", "category", "description")})
        return rows

    def _tool_describe_modifier(self, name: str) -> dict[str, Any]:
        if name not in self.registry:
            raise ToolError(f"modifier inconnu : {name} (voir list_modifiers)")
        return self.registry.get(name).describe(self.lang)

    def _tool_inspect_file(self, path: str, layer: str | None = None, count: bool = False) -> dict[str, Any]:
        return self.backend.describe_file(path, layer, bool(count))

    def _tool_list_dir(self, path: str = "") -> dict[str, Any]:
        return self.backend.list_dir(path)

    def _tool_get_flow(self) -> dict[str, Any]:
        if not self.flow.get("nodes"):
            return {"nodes": [], "links": [], "note": "le flux ouvert est vide"}
        flow = {
            k: v
            for k, v in self.flow.items()
            if k in ("name", "description", "nodes", "links", "variables", "published_params")
        }
        flow["nodes"] = [
            {k: n[k] for k in ("id", "type", "label", "params", "enabled") if k in n}
            for n in self.flow.get("nodes", [])
        ]
        return flow

    def _tool_analyze_flow(self) -> dict[str, Any]:
        try:
            result = edits.validate(self.flow, self.registry, self.lang)
        except edits.EditError as exc:
            raise ToolError(str(exc)) from None
        schemas = {}
        for key, schema in (result.get("schemas") or {}).items():
            fields = schema if isinstance(schema, list) else (schema or {}).get("fields", schema)
            schemas[key] = fields
        return {"issues": result["issues"], "schemas": schemas}

    def _run(self, run_id: str | None) -> str:
        wanted = run_id or self.run_id
        if not wanted:
            raise ToolError("aucun run : le flux n'a pas encore été lancé")
        return wanted

    def _tool_get_run_status(self, run_id: str | None = None) -> dict[str, Any]:
        return self.backend.run_info(self._run(run_id))

    def _tool_get_run_logs(self, run_id: str | None = None, limit: int = 40) -> list[dict[str, Any]]:
        rows = self.backend.run_logs(self._run(run_id), max(1, min(int(limit), 200)))
        return [{**r, "message": str(r.get("message", ""))[:300]} for r in rows]

    def _tool_get_port_sample(
        self, node: str, port: str = "output", run_id: str | None = None, rows: int = 5
    ) -> dict[str, Any]:
        run = self._run(run_id)
        info = self.backend.port_info(run, node, port)
        out: dict[str, Any] = {"node": node, "port": port, **info}
        if self.privacy.share_rows:
            out["rows"] = self.backend.port_rows(run, node, port, self.privacy.clamp_rows(rows))
        else:
            out["rows"] = "non transmises : mode « schéma seulement » (réglage de confidentialité du fournisseur)"
        return out

    # ------------------------------------------------------------------ actions : validation des propositions
    def propose(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Valide une proposition et renvoie ce que l'interface affichera. Lève `ToolError` si elle est invalide."""
        if name == "propose_flow_edits":
            return self._propose_edits(args)
        if name == "run_flow":
            node = args.get("until_node")
            if node and not any(n["id"] == node for n in self.flow.get("nodes", [])):
                raise ToolError(f"nœud introuvable : {node}")
            return {"until_node": node or None}
        if name == "write_modifier":
            return self._propose_modifier(args)
        if name == "create_component":
            return self._propose_component(args)
        raise ToolError(f"outil inconnu : {name}")

    def _propose_edits(self, args: dict[str, Any]) -> dict[str, Any]:
        operations = args.get("operations")
        if not isinstance(operations, list):
            raise ToolError("« operations » doit être une liste")
        try:
            result = edits.apply_operations(self.flow, operations, self.registry)
            analysis = edits.validate(result, self.registry, self.lang)
        except edits.EditError as exc:
            raise ToolError(str(exc)) from None
        unknown = self._unknown_params(result)
        if (
            unknown
        ):  # paramètre inventé : renvoyé au modèle, qui corrige seul (l'utilisateur ne le verrait qu'en erreur)
            lines = "\n".join(f"- {node} ({kind}) : {', '.join(names)}" for node, kind, names in unknown)
            raise ToolError(
                f"proposition refusée, paramètres inexistants :\n{lines}\n"
                "Lire les paramètres réels avec describe_modifier, puis proposer à nouveau."
            )
        return {
            "operations": operations,
            "summary": edits.describe_operations(operations),
            "flow": result,
            "issues": analysis["issues"],
            "explanation": str(args.get("explanation", "")),
        }

    def _unknown_params(self, result: dict[str, Any]) -> list[tuple[str, str, list[str]]]:
        """(nœud, type, paramètres inconnus) des nœuds ajoutés ou modifiés par la proposition. Un paramètre requis
        laissé vide reste permis : c'est souvent à l'utilisateur de le remplir (fichier à choisir…)."""
        before = {n["id"]: n for n in self.flow.get("nodes", [])}
        out = []
        for node in result.get("nodes", []):
            if before.get(node["id"]) == node or node.get("type") not in self.registry:
                continue
            params = self.registry.get(node["type"]).Params
            if params.model_config.get("extra") != "forbid":  # paramètres libres (composants…)
                continue
            fields = params.model_fields
            allowed = set(fields) | {f.alias for f in fields.values() if f.alias}
            extra = sorted(k for k in (node.get("params") or {}) if k not in allowed)
            if extra:
                out.append((node["id"], node["type"], extra))
        return out

    def _propose_modifier(self, args: dict[str, Any]) -> dict[str, Any]:
        name, code, test_code = args.get("name", ""), args.get("code", ""), args.get("test_code", "")
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise ToolError("« name » : nom de classe en CamelCase (lettres et chiffres, 3 à 41 caractères)")
        if name in self.registry:
            raise ToolError(f"un modificateur nommé {name} existe déjà : choisir un autre nom")
        for label, source in (("code", code), ("test_code", test_code)):
            if not isinstance(source, str) or not source.strip():
                raise ToolError(f"« {label} » est vide")
            try:
                tree = ast.parse(source)
            except SyntaxError as exc:
                raise ToolError(f"« {label} » : erreur de syntaxe ligne {exc.lineno} : {exc.msg}") from None
            if label == "code":
                classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == name]
                if not classes:
                    raise ToolError(f"« code » doit définir une classe {name}(Modifier)")
        return {"name": name, "code": code, "test_code": test_code, "explanation": str(args.get("explanation", ""))}

    def _propose_component(self, args: dict[str, Any]) -> dict[str, Any]:
        name = args.get("name", "")
        nodes, links = args.get("nodes") or [], args.get("links") or []
        flow = {
            "name": args.get("label") or name,
            "description": args.get("description", ""),
            "component": {
                "name": name,
                "label": args.get("label") or name,
                "category": args.get("category") or "Composants",
                "description": args.get("description", ""),
            },
            "nodes": nodes,
            "links": links,
        }
        flow = edits.with_defaults(flow)
        try:
            edits.validate(flow, self.registry, self.lang)
        except edits.EditError as exc:
            raise ToolError(str(exc)) from None
        types = {n.get("type") for n in nodes}
        if "ComponentInput" not in types and "ComponentOutput" not in types:
            raise ToolError("un composant a besoin d'au moins un nœud ComponentInput ou ComponentOutput")
        return {"flow": flow}
