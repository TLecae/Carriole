"""Bibliothèque de composants en fichiers.

Un composant est un fichier de flux JSON qui déclare `component` (voir `etl_engine.components`), rangé dans un
dossier de composants : `ETL_COMPONENTS_DIR` (plusieurs dossiers séparés par `os.pathsep`) et le réglage
`component_dirs`. Les dossiers sont typiquement sur le partage de l'équipe.

Versions (ADR 0017) : une version publiée est un instantané immuable rangé à côté du fichier, `<nom>.v<N>.json`,
dans une enveloppe `{"component_version": {version, note, author, created_at}, "flow": {...}}`. Le fichier du
composant lui-même est le brouillon, qu'un nœud suit tant qu'aucune version n'est publiée.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from etl_engine import atomic
from etl_engine import settings as user_settings
from etl_engine.flow import Flow

_VERSION_FILE = re.compile(r"^(?P<stem>.+)\.v(?P<n>\d+)\.json$")


class NothingToPublish(Exception):
    """Le composant n'a pas changé depuis sa dernière version publiée."""


class UnknownComponent(Exception):
    pass


@dataclass(frozen=True)
class Entry:
    name: str
    path: Path
    flow: Flow


_lock = threading.Lock()
_flows: dict[tuple[str, int, int], Flow | None] = {}  # (chemin, mtime_ns, taille) -> flux lu (None : illisible)


def component_dirs() -> list[Path]:
    raw = [p for p in os.environ.get("ETL_COMPONENTS_DIR", "").split(os.pathsep) if p.strip()]
    extra = user_settings.get("component_dirs", [])
    if isinstance(extra, list):
        raw += [str(p) for p in extra if str(p).strip()]
    return list(dict.fromkeys(Path(p) for p in raw))


def _read(path: Path) -> Flow | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    with _lock:
        if key in _flows:
            return _flows[key]
    try:
        flow: Flow | None = Flow.load(path)
    except (OSError, ValueError):
        flow = None
    with _lock:
        _flows[key] = flow
        if len(_flows) > 2000:  # borne : un dossier de composants reste petit
            _flows.pop(next(iter(_flows)))
    return flow


def scan() -> tuple[dict[str, Entry], dict[str, str]]:
    """(composants par nom, erreurs par fichier). Le premier fichier trouvé garde un nom en doublon."""
    found: dict[str, Entry] = {}
    errors: dict[str, str] = {}
    for folder in component_dirs():
        try:
            files = sorted(folder.glob("*.json"))
        except OSError as exc:
            errors[str(folder)] = f"dossier de composants inaccessible : {exc}"
            continue
        for path in files:
            if _VERSION_FILE.match(path.name):
                continue
            flow = _read(path)
            if flow is None or not flow.component:
                continue  # pas un composant (ou illisible : un flux ordinaire du même dossier n'est pas une erreur)
            name = flow.component.name
            if name in found:
                errors[str(path)] = f"le composant « {name} » existe déjà dans {found[name].path}"
            else:
                found[name] = Entry(name, path, flow)
    return found, errors


def find_entry(name: str) -> Entry | None:
    return scan()[0].get(name)


# ------------------------------------------------------------------------------------------ versions


def _version_files(entry: Entry) -> list[tuple[int, Path]]:
    out = []
    try:
        for path in entry.path.parent.glob(f"{entry.path.stem}.v*.json"):
            m = _VERSION_FILE.match(path.name)
            if m and m["stem"] == entry.path.stem:
                out.append((int(m["n"]), path))
    except OSError:
        return []
    return sorted(out, reverse=True)


def _envelope(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        meta = data["component_version"]
        meta["version"] = int(meta["version"])
        return {**meta, "flow": data["flow"]}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def list_versions(name: str) -> list[dict[str, Any]]:
    """Versions publiées, de la plus récente à la plus ancienne (sans contenu)."""
    entry = find_entry(name)
    if entry is None:
        return []
    out = []
    for _n, path in _version_files(entry):
        env = _envelope(path)
        if env:
            out.append({k: env.get(k) for k in ("name", "version", "note", "author", "created_at")} | {"name": name})
    return out


def get_version(name: str, version: int | None = None) -> dict[str, Any] | None:
    """Une version publiée avec son contenu (`flow`) ; `version=None` : la dernière."""
    entry = find_entry(name)
    if entry is None:
        return None
    for n, path in _version_files(entry):
        if version is None or n == version:
            env = _envelope(path)
            return {**env, "name": name} if env else None
    return None


def latest_versions() -> dict[str, int]:
    out: dict[str, int] = {}
    for name, entry in scan()[0].items():
        files = _version_files(entry)
        if files:
            out[name] = files[0][0]
    return out


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)


def publish(name: str, snapshot: dict[str, Any], note: str, author: str) -> dict[str, Any]:
    """Nouvelle version (instantané `snapshot`) ; refuse si rien n'a changé depuis la précédente."""
    entry = find_entry(name)
    if entry is None:
        raise UnknownComponent(name)
    files = _version_files(entry)
    if files:
        last = _envelope(files[0][1])
        if last and _same(last["flow"], snapshot):
            raise NothingToPublish(f"la version {last['version']} est identique au composant actuel")
    meta = {"version": (files[0][0] + 1) if files else 1, "note": note, "author": author, "created_at": time.time()}
    while True:  # deux publications simultanées : la seconde prend le numéro suivant
        target = entry.path.with_name(f"{entry.path.stem}.v{meta['version']}.json")
        text = json.dumps({"component_version": meta, "flow": snapshot}, ensure_ascii=False, indent=2, sort_keys=True)
        try:
            atomic.write_text(target, text + "\n", create_only=True)
            return {"name": name, **meta}
        except FileExistsError:
            meta["version"] += 1


# ------------------------------------------------------------------------------------------ moteur


def provider(name: str, version: int | None) -> tuple[Flow, int | None] | None:
    """Fournisseur pour `etl_engine.components` : version publiée demandée (ou la dernière), sinon le brouillon."""
    published = get_version(name, version)
    if published is not None:
        return Flow.model_validate(published["flow"]), published["version"]
    if version is not None:
        return None  # version demandée inexistante
    entry = find_entry(name)
    return (entry.flow, None) if entry else None
