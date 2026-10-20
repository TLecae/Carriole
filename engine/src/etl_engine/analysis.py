"""Analyse d'un flux sans l'exécuter : avertissements et schémas par port (pour proposer les attributs en aval)."""

from __future__ import annotations

import pyarrow as pa

from etl_engine import i18n
from etl_engine.components import PREFIX as COMPONENT_PREFIX
from etl_engine.components import SEP, expand_components
from etl_engine.data import schema_to_json
from etl_engine.flow import Flow, late_names, resolve_params, system_values
from etl_engine.plan import Issue, Plan, build_plan
from etl_engine.registry import Registry
from etl_engine.sdk import ConfigError, Context

SchemaMap = dict[tuple[str, str], pa.Schema | None]


def compute_schemas(plan: Plan) -> tuple[SchemaMap, list[Issue]]:
    """Schéma de chaque port de sortie, dans l'ordre du flux, et problèmes détectés."""
    schemas: SchemaMap = {}
    issues: list[Issue] = []
    for nid in plan.order:
        inst = plan.nodes[nid]
        inputs: dict[str, pa.Schema | None] = {}
        for link in plan.inbound.get(nid, []):
            inputs.setdefault(link.to[1], schemas.get(link.from_))
        known = inputs.get(inst.inputs[0]) if inst.inputs else None
        if known is not None:
            missing = sorted({a for a in inst.used_attributes() if a not in known.names})
            for attr in missing:
                issues.append(Issue("warning", "missing_attribute", f"attribut absent en amont : {attr}", nid))
        try:
            outs = inst.output_schema(inputs)
        except ConfigError as exc:
            issues.append(Issue("error", "config", str(exc), nid))
            outs = {}
        except Exception as exc:  # noqa: BLE001 - un modificateur ne doit pas casser l'analyse des autres
            issues.append(Issue("warning", "schema_unknown", f"schéma de sortie non calculable ({exc})", nid))
            outs = {}
        for port in inst.ports(inst.params)[1]:
            schemas[(nid, port)] = outs.get(port)
    return schemas, issues


def unset_variables(plan: Plan) -> list[Issue]:
    """`${var:NOM}` n'a de valeur que si un SetVariables qui pose NOM est en amont du nœud qui le lit."""
    setters: dict[str, set[str]] = {}
    for nid, inst in plan.nodes.items():
        for spec in getattr(inst.params, "variables", None) or []:
            if type(inst).__name__ == "SetVariables":
                setters.setdefault(getattr(spec, "name", ""), set()).add(nid)
    issues = []
    for nid, raw in plan.late.items():
        upstream = plan.upstream(nid)
        for name in sorted(late_names(raw)):
            if not setters.get(name, set()) & upstream:
                issues.append(
                    Issue("error", "var_unset", f"variable ${{var:{name}}} : aucun nœud ne la pose en amont", nid)
                )
    return issues


def analyze(flow: Flow, registry: Registry, overrides: dict[str, str] | None = None, lang: str | None = None) -> dict:
    """Avertissements avant exécution et schéma de chaque port, au format JSON pour l'éditeur (messages dans `lang`)."""
    result = _analyze(flow, registry, overrides)
    return i18n.translate_json(result, lang, frozenset({"message"}))


def _analyze(flow: Flow, registry: Registry, overrides: dict[str, str] | None) -> dict:
    try:
        values = resolve_params(flow, overrides, system_values(preview=True))
        problems: list[Issue] = []
    except ValueError as exc:
        values = {p.name: p.default for p in flow.published_params}
        problems = [Issue("warning", "params", str(exc))]

    component_nodes = [n.id for n in flow.nodes if n.type.startswith(COMPONENT_PREFIX)]
    try:
        flow, aliases = expand_components(flow)
    except ConfigError as exc:
        return {"issues": [Issue("error", "component", str(exc)).to_json()], "schemas": {}, "order": [], "aliases": {}}
    plan = build_plan(flow, registry, values, lambda nid: Context(node_id=nid, params=values))
    schemas, issues = compute_schemas(plan)
    issues.extend(unset_variables(plan))
    for (outer, port), inner in aliases.items():  # un port de nœud composant a le schéma du port interne
        if inner in schemas:
            schemas[(outer, port)] = schemas[inner]
    for issue in [*plan.issues, *issues]:  # un problème interne à un composant est rattaché à son nœud sur le canvas
        for outer in component_nodes:
            if issue.node and issue.node.startswith(outer + SEP):
                issue.message = f"{issue.node[len(outer) + len(SEP) :]} : {issue.message}"
                issue.node = outer
                break
    return {
        "issues": [i.to_json() for i in [*problems, *plan.issues, *issues]],
        "schemas": {f"{n}/{p}": schema_to_json(s) for (n, p), s in schemas.items()},
        "order": plan.order,
        # ports réels de chaque nœud (certains dépendent des paramètres : RouteByExpression, ExecuteSql…)
        "ports": {
            nid: dict(zip(("inputs", "outputs"), map(list, inst.ports(inst.params)), strict=True))
            for nid, inst in plan.nodes.items()
        },
        "aliases": {f"{o}/{p}": f"{i[0]}/{i[1]}" for (o, p), i in aliases.items()},
    }
