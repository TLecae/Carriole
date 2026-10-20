"""Du flux JSON à un plan exécutable : instanciation des modificateurs, liens valides, ordre topologique."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import ValidationError

from etl_engine.flow import Flow, Link, expand, has_late
from etl_engine.registry import Registry
from etl_engine.sdk import Context, Modifier


@dataclass
class Issue:
    level: str  # error | warning
    code: str
    message: str
    node: str | None = None

    def to_json(self) -> dict[str, str | None]:
        return {"level": self.level, "code": self.code, "message": self.message, "node": self.node}


@dataclass
class Plan:
    flow: Flow
    values: dict[str, str]
    nodes: dict[str, Modifier] = field(default_factory=dict)  # nœuds actifs et valides
    late: dict[str, dict] = field(default_factory=dict)  # nœud -> paramètres qui citent ${var:…} (validés au démarrage)
    order: list[str] = field(default_factory=list)  # ordre topologique
    inbound: dict[str, list[Link]] = field(default_factory=dict)  # liens actifs, par nœud cible
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    def upstream(self, node_id: str) -> set[str]:
        """Tous les ancêtres d'un nœud."""
        seen: set[str] = set()
        todo = [node_id]
        while todo:
            for link in self.inbound.get(todo.pop(), []):
                if link.from_[0] not in seen:
                    seen.add(link.from_[0])
                    todo.append(link.from_[0])
        return seen


def _describe(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:3]:
        where = ".".join(str(p) for p in err["loc"]) or "paramètres"
        parts.append(f"{where} : {err['msg']}")
    return " ; ".join(parts)


def build_plan(flow: Flow, registry: Registry, values: dict[str, str], ctx_for: Callable[[str], Context]) -> Plan:
    plan = Plan(flow=flow, values=values)
    issue = plan.issues.append

    disabled = {n.id for n in flow.nodes if not n.enabled}
    for node in flow.nodes:
        if not node.enabled:
            continue
        if node.type not in registry:
            issue(Issue("error", "unknown_type", f"modifier inconnu : {node.type}", node.id))
            continue
        cls = registry.get(node.type)
        try:
            expanded = expand(node.params, values)
        except ValueError as exc:
            issue(Issue("error", "invalid_params", str(exc), node.id))
            continue
        try:
            params = cls.Params.model_validate(expanded)
        except ValidationError as exc:
            if not has_late(expanded):
                issue(Issue("error", "invalid_params", _describe(exc), node.id))
                continue
            # une variable posée en cours de run n'a pas encore de valeur : les champs non texte seront validés
            # au démarrage du nœud ; l'analyse se contente des paramètres tels quels
            params = cls.Params.model_construct(**expanded)
        except ValueError as exc:
            issue(Issue("error", "invalid_params", str(exc), node.id))
            continue
        if has_late(expanded):
            plan.late[node.id] = expanded
        plan.nodes[node.id] = cls(params, ctx_for(node.id))

    inbound: dict[str, list[Link]] = defaultdict(list)
    for link in flow.links:
        (src, src_port), (dst, dst_port) = link.from_, link.to
        if not link.enabled or src in disabled or dst in disabled:
            continue
        known = {n.id for n in flow.nodes}
        if src not in known or dst not in known:
            issue(Issue("error", "dangling_link", f"lien {link.id} : nœud introuvable", dst if dst in known else src))
            continue
        if src not in plan.nodes or dst not in plan.nodes:
            continue  # extrémité invalide : déjà signalée
        outs = plan.nodes[src].ports(plan.nodes[src].params)[1]
        ins = plan.nodes[dst].ports(plan.nodes[dst].params)[0]
        if src_port not in outs:
            issue(Issue("error", "bad_port", f"{src} n'a pas de sortie « {src_port} »", src))
        elif dst_port not in ins:
            issue(Issue("error", "bad_port", f"{dst} n'a pas d'entrée « {dst_port} »", dst))
        else:
            inbound[dst].append(link)
    plan.inbound = dict(inbound)

    for nid, inst in plan.nodes.items():
        inputs = inst.ports(inst.params)[0]
        connected = {link.to[1] for link in plan.inbound.get(nid, [])}
        for port in inputs:
            if port not in connected:
                issue(Issue("warning", "unconnected_input", f"entrée « {port} » non connectée", nid))

    # ordre topologique (Kahn), stable : à égalité, l'ordre des nœuds dans le flux
    position = {n.id: i for i, n in enumerate(flow.nodes)}
    indegree = {nid: len({link.from_[0] for link in plan.inbound.get(nid, [])}) for nid in plan.nodes}
    children: dict[str, set[str]] = defaultdict(set)
    for nid, links in plan.inbound.items():
        for link in links:
            children[link.from_[0]].add(nid)
    queue = deque(sorted((n for n, d in indegree.items() if d == 0), key=position.get))
    while queue:
        nid = queue.popleft()
        plan.order.append(nid)
        for child in sorted(children[nid], key=position.get):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    stuck = [n for n in plan.nodes if n not in plan.order]
    if stuck:
        issue(Issue("error", "cycle", f"boucle dans le flux : {', '.join(stuck)}", stuck[0]))
    return plan
