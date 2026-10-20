"""Format natif d'un flux : JSON lisible, stable, versionnable dans git (voir ADR 0002)."""

from __future__ import annotations

import getpass
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from etl_engine import renames

SCHEMA_VERSION = 1
_VAR = re.compile(r"\$\{([^}]+)\}")
_LATE = re.compile(r"\$\{var:([^}]+)\}")
VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PublishedParam(BaseModel):
    """Paramètre saisi au lancement (interface ou `--param`) ; référencé par `${NOM}`."""

    name: str
    label: str = ""
    type: Literal["text", "number", "bool", "choice", "path", "connection"] = "text"
    default: str = ""
    required: bool = False
    choices: list[str] = []


class Node(BaseModel):
    id: str
    type: str
    label: str = ""
    params: dict[str, Any] = {}
    position: tuple[float, float] = (0.0, 0.0)
    enabled: bool = True
    component_version: int | None = None  # nœud composant : version publiée épinglée (None : la dernière)


class Link(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    from_: tuple[str, str] = Field(alias="from")  # (nœud, port de sortie)
    to: tuple[str, str]  # (nœud, port d'entrée)
    enabled: bool = True


class Annotation(BaseModel):
    id: str
    text: str
    position: tuple[float, float] = (0.0, 0.0)


class Bookmark(BaseModel):
    id: str
    name: str
    color: str = "#cfe3ff"
    nodes: list[str] = []
    collapsed: bool = False


class ComponentMeta(BaseModel):
    """Un flux qui porte ceci est un composant réutilisable (voir `etl_engine.components`)."""

    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,59}$")  # identifiant stable des nœuds `component:<name>`
    label: str = ""
    category: str = ""
    description: str = ""


class Flow(BaseModel):
    schema_version: int = SCHEMA_VERSION
    name: str = ""
    description: str = ""
    published_params: list[PublishedParam] = []
    variables: dict[str, str] = {}  # constantes du flux ou valeurs composées d'autres variables (${AUTRE})
    nodes: list[Node] = []
    links: list[Link] = []
    annotations: list[Annotation] = []
    bookmarks: list[Bookmark] = []
    component: ComponentMeta | None = None  # présent : ce flux est un composant
    subflows: dict[str, Any] = {}  # définitions des composants utilisés, figées pour un run

    @model_validator(mode="before")
    @classmethod
    def _per_format_readers(cls, data: Any) -> Any:
        """Les anciens « Lecture / Écriture de fichier » (tous formats) deviennent le lecteur / l'écrivain de leur
        format, avec ses seuls réglages (voir `modifiers.file_io`). Les types renommés le 2026-10-20 prennent leur
        nouveau nom (voir `renames`)."""
        if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
            return data
        data = renames.migrate(data)
        if not any(isinstance(n, dict) and n.get("type") in ("Reader", "Writer") for n in data["nodes"]):
            return data
        from etl_engine.modifiers.file_io import convert_legacy_node

        nodes = [convert_legacy_node(n) if isinstance(n, dict) else n for n in data["nodes"]]
        return {**data, "nodes": nodes}

    @model_validator(mode="after")
    def _unique_ids(self) -> Flow:
        for kind, items in (("nœud", self.nodes), ("lien", self.links)):
            ids = [i.id for i in items]
            dup = {i for i in ids if ids.count(i) > 1}
            if dup:
                raise ValueError(f"identifiants de {kind} en double : {sorted(dup)}")
        return self

    def node(self, node_id: str) -> Node:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(node_id)

    # --- fichiers ----------------------------------------------------------------------
    def dumps(self) -> str:
        """JSON stable : ordre des nœuds et des liens conservé, clés triées, fin de ligne unique."""
        data = self.model_dump(mode="json", by_alias=True)
        return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.dumps(), encoding="utf-8", newline="\n")

    @classmethod
    def loads(cls, text: str) -> Flow:
        data = json.loads(text)
        version = data.get("schema_version", SCHEMA_VERSION)
        if version > SCHEMA_VERSION:
            raise ValueError(f"flux en version {version}, cette version du moteur gère jusqu'à {SCHEMA_VERSION}")
        return cls.model_validate(data)

    @classmethod
    def load(cls, path: str | Path) -> Flow:
        return cls.loads(Path(path).read_text(encoding="utf-8"))


def system_values(
    flow_path: str | Path | None = None, run_id: str | None = None, user: str | None = None, preview: bool = False
) -> dict[str, str]:
    """Variables `${sys:…}` : date, utilisateur, dossier du flux, numéro du run… `preview` : valeurs d'exemple pour
    l'analyse, qui n'a ni run ni (toujours) fichier de flux."""
    now = datetime.now()
    try:
        login = user or getpass.getuser()
    except Exception:  # noqa: BLE001 - pas de nom d'utilisateur sur ce poste
        login = user or ""
    out = {
        "sys:date": now.strftime("%Y-%m-%d"),
        "sys:datetime": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "sys:timestamp": now.strftime("%Y%m%d_%H%M%S"),
        "sys:user": login,
        "sys:temp_dir": tempfile.gettempdir(),
    }
    if flow_path:
        p = Path(flow_path)
        out.update({"sys:flow_path": str(p), "sys:flow_dir": str(p.parent), "sys:flow_name": p.stem})
    elif preview:
        out.update({"sys:flow_path": "", "sys:flow_dir": "", "sys:flow_name": ""})
    if run_id:
        out["sys:run_id"] = run_id
    elif preview:
        out["sys:run_id"] = "(run)"
    return out


def global_values() -> dict[str, str]:
    """Variables `${global:NOM}` : le réglage `variables` de l'utilisateur (settings.json)."""
    from etl_engine import settings

    raw = settings.get("variables", {})
    return {f"global:{k}": str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def resolve_params(
    flow: Flow, overrides: dict[str, str] | None = None, system: dict[str, str] | None = None
) -> dict[str, str]:
    """Valeurs de toutes les variables `${…}` d'un flux, dans l'ordre : paramètres publiés (défaut du flux <
    variables ETL_PARAM_* < `overrides`), puis variables du flux (composables), `sys:` et `global:`."""
    values = {p.name: p.default for p in flow.published_params}
    for key, value in os.environ.items():
        if key.startswith("ETL_PARAM_") and key[10:] in values:
            values[key[10:]] = value
    values.update(overrides or {})
    missing = [p.name for p in flow.published_params if p.required and values.get(p.name, "") == ""]
    if missing:
        raise ValueError(f"paramètres publiés obligatoires manquants : {missing}")
    unknown = sorted(set(overrides or {}) - {p.name for p in flow.published_params})
    if unknown:
        raise ValueError(f"paramètres inconnus : {unknown}")
    values.update(global_values())
    values.update(system if system is not None else system_values(preview=True))
    values.update(_resolve_variables(flow, values))
    return values


def _resolve_variables(flow: Flow, values: dict[str, str]) -> dict[str, str]:
    """Variables du flux : une valeur peut en citer d'autres (`${AUTRE}`), dans n'importe quel ordre."""
    pending = dict(flow.variables)
    for name in pending:
        if not VARIABLE_NAME.match(name):
            raise ValueError(f"nom de variable invalide : « {name} » (lettres, chiffres et _)")
        if name in values:
            raise ValueError(f"variable « {name} » : ce nom est déjà un paramètre publié")
    resolved: dict[str, str] = {}
    while pending:
        ready = [n for n, raw in pending.items() if not ({m.group(1) for m in _VAR.finditer(raw)} & pending.keys())]
        if not ready:
            raise ValueError(f"variables circulaires : {', '.join(sorted(pending))}")
        for name in ready:
            resolved[name] = expand(pending.pop(name), {**values, **resolved})
    return resolved


def has_late(value: Any) -> bool:
    """Vrai si une valeur cite une variable posée en cours de run (`${var:NOM}`)."""
    if isinstance(value, str):
        return bool(_LATE.search(value))
    if isinstance(value, dict):
        return any(has_late(v) for v in value.values())
    if isinstance(value, list):
        return any(has_late(v) for v in value)
    return False


def late_names(value: Any) -> set[str]:
    if isinstance(value, str):
        return {m.group(1) for m in _LATE.finditer(value)}
    if isinstance(value, dict):
        return set().union(*(late_names(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(late_names(v) for v in value)) if value else set()
    return set()


def expand_late(value: Any, variables: dict[str, str]) -> Any:
    """Remplace `${var:NOM}` par la valeur posée par un SetVariables ; le reste de la valeur n'est pas retouché."""
    if isinstance(value, str):

        def repl(m: re.Match[str]) -> str:
            if m.group(1) not in variables:
                raise ValueError(f"variable ${{var:{m.group(1)}}} jamais posée avant ce nœud")
            return variables[m.group(1)]

        return _LATE.sub(repl, value)
    if isinstance(value, dict):
        return {k: expand_late(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_late(v, variables) for v in value]
    return value


def expand(value: Any, variables: dict[str, str]) -> Any:
    """Remplace `${NOM}` (paramètre ou variable du flux), `${env:NOM}`, `${sys:NOM}` et `${global:NOM}` dans toute
    valeur texte. `${var:NOM}` reste tel quel : il est résolu au démarrage du nœud (`expand_late`)."""
    if isinstance(value, str):

        def repl(m: re.Match[str]) -> str:
            key = m.group(1)
            if key.startswith("var:"):
                return m.group(0)
            if key.startswith("env:"):
                name = key[4:]
                if name not in os.environ:
                    raise ValueError(f"variable d'environnement absente : {name}")
                return os.environ[name]
            if key not in variables:
                if key.startswith("sys:"):
                    raise ValueError(f"variable système indisponible : ${{{key}}} (flux non enregistré ?)")
                if key.startswith("global:"):
                    raise ValueError(f"variable globale inconnue : ${{{key}}} (réglage « variables »)")
                raise ValueError(f"paramètre publié inconnu : ${{{key}}}")
            return variables[key]

        return _VAR.sub(repl, value)
    if isinstance(value, dict):
        return {k: expand(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v, variables) for v in value]
    return value
