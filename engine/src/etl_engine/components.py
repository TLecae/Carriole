"""Composants (custom modificateurs) : un flux réutilisable comme un modificateur.

Un flux devient un composant en déclarant `component` (nom, libellé, catégorie). Ses nœuds `ComponentInput` et
`ComponentOutput` définissent ses **ports** ; ses paramètres publiés deviennent les **paramètres** du composant.
Dans un autre flux, un nœud de type `component:<nom>` se comporte comme un modificateur : à l'analyse et à
l'exécution, le moteur le **remplace par le contenu du composant** (`expand_components`), les ports du composant
étant raccordés aux liens du nœud. Aucune autre partie du moteur ne connaît les composants.

Les composants sont fournis par des fournisseurs (`install_provider`) ; à défaut, par les dossiers de composants
(`etl_engine.complib` : fichiers JSON et leurs versions, ligne de commande, partage de l'équipe). Un composant a des
**versions publiées** (instantanés immuables) ; un nœud en épingle une (`Node.component_version`) ou suit la dernière
(`None`), et, tant qu'aucune version n'est publiée, le flux lui-même (brouillon). Un run embarque la version utilisée
de chaque composant dans `Flow.subflows` (clé `nom@N`, ou `nom` pour un brouillon) : le relancer plus tard rejoue
exactement la même chose.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from etl_engine import complib
from etl_engine.flow import Flow, Link, Node
from etl_engine.sdk import ConfigError

PREFIX = "component:"
SEP = "__"  # séparateur entre l'identifiant du nœud composant et celui du nœud interne
INPUT, OUTPUT = "ComponentInput", "ComponentOutput"
MAX_DEPTH = 8
_VAR = re.compile(r"\$\{([^}:]+)\}")

# fournisseur : (nom, version demandée) -> (définition, version trouvée) ; version None : la dernière publiée, à défaut
# le brouillon (version trouvée None)
Provider = Callable[[str, int | None], tuple[Flow, int | None] | None]
_providers: dict[str, Provider] = {}


def install_provider(provider: Provider, key: str = "default") -> None:
    _providers[key] = provider


def clear_providers() -> None:
    _providers.clear()


def key_of(name: str, version: int | None) -> str:
    """Clé d'une définition dans `Flow.subflows`."""
    return name if version is None else f"{name}@{version}"


def resolve(
    name: str, version: int | None = None, flow: Flow | None = None, definitions: dict[str, Any] | None = None
) -> tuple[Flow, int | None] | None:
    """(définition, version) d'un composant : celle embarquée dans `flow.subflows`, sinon celle d'un fournisseur."""
    embedded = {**(flow.subflows if flow is not None else {}), **(definitions or {})}
    if key_of(name, version) in embedded:
        return Flow.model_validate(embedded[key_of(name, version)]), version
    for provider in [*_providers.values(), complib.provider]:
        found = provider(name, version)
        if found is not None:
            return found
    return None


def find(name: str, flow: Flow | None = None, version: int | None = None) -> Flow | None:
    """Définition du composant (sans son numéro de version) ; voir `resolve`."""
    found = resolve(name, version, flow)
    return found[0] if found else None


def embed_components(flow: Flow) -> Flow:
    """Copie de `flow` qui embarque la définition des composants utilisés (figée pour le run).

    Chaque nœud composant est épinglé à la version résolue (la dernière publiée si elle n'était pas choisie), y
    compris dans les composants imbriqués : la copie ne dépend plus de l'état ultérieur de la base.
    """
    definitions: dict[str, Any] = dict(flow.subflows)

    def pin(f: Flow) -> Flow:
        nodes: list[Node] = []
        for node in f.nodes:
            found = resolve(node.type[len(PREFIX) :], node.component_version, None, definitions) if _is(node) else None
            if found is None:
                nodes.append(node)
                continue
            component, version = found
            key = key_of(node.type[len(PREFIX) :], version)
            if key not in definitions:
                definitions[key] = {}  # garde-fou contre un composant qui s'appelle lui-même
                for k, v in component.subflows.items():
                    definitions.setdefault(k, v)
                pinned = pin(component).model_copy(update={"subflows": {}})
                definitions[key] = pinned.model_dump(mode="json", by_alias=True)
            nodes.append(node.model_copy(update={"component_version": version}))
        return f.model_copy(update={"nodes": nodes})

    pinned = pin(flow)
    return pinned.model_copy(update={"subflows": definitions}) if definitions != flow.subflows else pinned


def _is(node: Node) -> bool:
    return node.type.startswith(PREFIX)


# --------------------------------------------------------------------------------- description


def ports_of(component: Flow) -> tuple[list[str], list[str]]:
    """(entrées, sorties) d'un composant : ports des nœuds ComponentInput / ComponentOutput, dans l'ordre du flux."""
    ins = [str(n.params.get("port", "")) for n in component.nodes if n.type == INPUT]
    outs = [str(n.params.get("port", "")) for n in component.nodes if n.type == OUTPUT]
    return [p for p in dict.fromkeys(ins) if p], [p for p in dict.fromkeys(outs) if p]


def describe(name: str, component: Flow) -> dict[str, Any]:
    """Même forme que `Modifier.describe()` : la palette et le formulaire n'ont rien de particulier à faire."""
    meta = component.component
    ins, outs = ports_of(component)
    widgets = {"path": "path", "connection": "connection"}
    properties: dict[str, Any] = {}
    for p in component.published_params:
        spec: dict[str, Any] = {"title": p.label or p.name, "type": "string", "default": p.default}
        if p.type in widgets:
            spec["x-etl-widget"] = widgets[p.type]
        if p.type == "choice" and p.choices:
            spec["enum"] = p.choices
        if p.type == "bool":
            spec = {
                "title": p.label or p.name,
                "type": "string",
                "enum": ["true", "false"],
                "default": p.default or "false",
            }
        properties[p.name] = spec
    return {
        "name": PREFIX + name,
        "label": (meta.label if meta and meta.label else name),
        "category": (meta.category if meta and meta.category else "Composants"),
        "category_id": (meta.category if meta and meta.category else "Composants"),
        "description": (meta.description if meta and meta.description else component.description) or "Composant",
        "help": component.description,
        "version": 1,
        "mode": "streaming",
        "inputs": ins,
        "outputs": outs,
        "component": True,
        "params_schema": {
            "type": "object",
            "properties": properties,
            "required": [p.name for p in component.published_params if p.required],
            "additionalProperties": False,
        },
    }


# ---------------------------------------------------------------------------------- expansion


def _substitute(value: Any, values: dict[str, str]) -> Any:
    """Remplace `${NOM}` par la valeur du paramètre du composant ; les autres `${…}` restent pour le flux parent."""
    if isinstance(value, str):
        return _VAR.sub(lambda m: values[m.group(1)] if m.group(1) in values else m.group(0), value)
    if isinstance(value, list):
        return [_substitute(v, values) for v in value]
    if isinstance(value, dict):
        return {k: _substitute(v, values) for k, v in value.items()}
    return value


def expand_components(
    flow: Flow, _depth: int = 0, _stack: tuple[str, ...] = ()
) -> tuple[Flow, dict[tuple[str, str], tuple[str, str]]]:
    """Remplace chaque nœud composant par le contenu de son composant.

    Renvoie le flux étendu et les **alias** `(nœud composant, port de sortie) -> (nœud interne, port)`, qui servent
    à retrouver, pour un port du nœud composant, les données du port interne qui l'alimente.
    Un composant introuvable est laissé tel quel : l'analyse le signalera comme « modificateur inconnu ».
    """
    if not any(n.type.startswith(PREFIX) for n in flow.nodes):
        return flow, {}
    if _depth > MAX_DEPTH:
        raise ConfigError(f"composants imbriqués sur plus de {MAX_DEPTH} niveaux : {' > '.join(_stack)}")

    nodes: list[Node] = []
    links: list[Link] = list(flow.links)
    aliases: dict[tuple[str, str], tuple[str, str]] = {}
    for node in flow.nodes:
        if not node.type.startswith(PREFIX):
            nodes.append(node)
            continue
        name = node.type[len(PREFIX) :]
        component = find(name, flow, node.component_version)
        if component is None:
            nodes.append(node)
            continue
        if name in _stack:
            raise ConfigError(f"composant récursif : {' > '.join((*_stack, name))}")
        # les définitions embarquées dans le flux parent restent disponibles pour les composants imbriqués
        shared = component.model_copy(update={"subflows": {**flow.subflows, **component.subflows}})
        inner, inner_aliases = expand_components(shared, _depth + 1, (*_stack, name))
        values = {p.name: p.default for p in component.published_params}
        values.update({k: str(v) for k, v in node.params.items()})

        def inner_id(x: str, node: Node = node) -> str:
            return f"{node.id}{SEP}{x}"

        boundary_in = {str(n.params.get("port", "")): n.id for n in inner.nodes if n.type == INPUT}
        boundary_out = {str(n.params.get("port", "")): n.id for n in inner.nodes if n.type == OUTPUT}
        boundary_ids = {*boundary_in.values(), *boundary_out.values()}
        for n in inner.nodes:
            if n.id in boundary_ids:
                continue
            nodes.append(
                n.model_copy(
                    update={
                        "id": inner_id(n.id),
                        "params": _substitute(n.params, values),
                        "enabled": n.enabled and node.enabled,
                    }
                )
            )
        # liens internes ordinaires (hors frontières), puis raccords avec les liens du nœud composant
        new_links: list[Link] = []
        for lk in inner.links:
            if lk.from_[0] in boundary_ids or lk.to[0] in boundary_ids:
                continue
            new_links.append(
                lk.model_copy(
                    update={
                        "id": inner_id(lk.id),
                        "from_": (inner_id(lk.from_[0]), lk.from_[1]),
                        "to": (inner_id(lk.to[0]), lk.to[1]),
                    }
                )
            )
        outer_in = [lk for lk in links if lk.to[0] == node.id]
        outer_out = [lk for lk in links if lk.from_[0] == node.id]
        for port, bid in boundary_in.items():
            targets = [lk for lk in inner.links if lk.from_[0] == bid]
            for src in (lk for lk in outer_in if lk.to[1] == port):
                for t in targets:
                    new_links.append(
                        Link.model_validate(
                            {
                                "id": f"{src.id}{SEP}{t.id}",
                                "from": src.from_,
                                "to": (inner_id(t.to[0]), t.to[1]),
                                "enabled": src.enabled and t.enabled,
                            }
                        )
                    )
        for port, bid in boundary_out.items():
            sources = [lk for lk in inner.links if lk.to[0] == bid]
            for dst in (lk for lk in outer_out if lk.from_[1] == port):
                for s in sources:
                    new_links.append(
                        Link.model_validate(
                            {
                                "id": f"{s.id}{SEP}{dst.id}",
                                "from": (inner_id(s.from_[0]), s.from_[1]),
                                "to": dst.to,
                                "enabled": dst.enabled and s.enabled,
                            }
                        )
                    )
            if sources:
                aliases[(node.id, port)] = (inner_id(sources[0].from_[0]), sources[0].from_[1])
        # alias des composants imbriqués : la source interne peut elle-même être un port de composant
        for (n_id, p), target in inner_aliases.items():
            aliases[(inner_id(n_id), p)] = (inner_id(target[0]), target[1])
        links = [lk for lk in links if lk.to[0] != node.id and lk.from_[0] != node.id] + new_links
    return flow.model_copy(update={"nodes": nodes, "links": links}), aliases
