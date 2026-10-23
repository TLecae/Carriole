"""Flux et composants en fichiers (poste de bureau).

Un flux est un fichier JSON rangé où l'équipe veut (le plus souvent sur le partage), et
l'application ne garde que la liste des flux récents. Les runs, événements, connexions et planifications restent dans
la base locale (`Store`) : ils sont propres au poste.

L'identifiant d'un flux est son chemin, encodé pour tenir dans une URL (`#flow=<id>`) : un run ou une planification
retrouve ainsi son flux. La « révision » est l'empreinte du contenu : l'enregistrement échoue (`Conflict`) si le fichier
a changé sur le disque depuis qu'on l'a lu, par exemple parce qu'un collègue l'a enregistré entre-temps.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from etl_engine import atomic, complib
from etl_engine.flow import Flow

from etl_store.store import Conflict, NotFound, Store

MAX_RECENT = 50


class FileAccessError(Exception):
    """Le fichier du flux est inaccessible (partage non joignable, droits, fichier verrouillé)."""


class InvalidFlowFile(Exception):
    """Le fichier n'est pas un flux lisible par cette version."""


def make_id(path: str | Path) -> str:
    raw = os.path.abspath(path).encode("utf-8")
    return "f" + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def flow_path(identifier: str) -> Path:
    if not identifier.startswith("f") or len(identifier) < 2:
        raise NotFound(identifier)
    try:
        padded = identifier[1:] + "=" * (-len(identifier[1:]) % 4)
        return Path(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeError):
        raise NotFound(identifier) from None


def _revision(raw: bytes) -> str:
    return hashlib.sha1(raw).hexdigest()[:16]  # noqa: S324 - empreinte de contenu, pas de la sécurité


def _text(content: dict[str, Any]) -> str:
    return json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True) + "\n"  # même rendu que Flow.dumps()


def _named(path: Path, content: dict[str, Any]) -> dict[str, Any]:
    """Le flux porte le nom de son fichier (sans « .json ») : un seul nom, celui que l'utilisateur a choisi en
    l'enregistrant ; on renomme un flux par « Enregistrer sous… »."""
    return {**content, "name": path.stem}


def _component_of(content: dict[str, Any]) -> str | None:
    meta = content.get("component")
    return str(meta["name"]) if isinstance(meta, dict) and meta.get("name") else None


class FileStore(Store):
    """`Store` dont les flux et les composants sont des fichiers."""

    def __init__(self, url: str, recents_file: Path) -> None:
        super().__init__(url)
        self._recents_file = Path(recents_file)
        self._recents_lock = threading.Lock()

    def components_provider(self):
        return None  # le moteur lit lui-même les dossiers de composants (etl_engine.complib)

    # ---------------------------------------------------------------- récents
    def _recents(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self._recents_file.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _remember(self, path: Path, content: dict[str, Any]) -> None:
        card = {
            "path": str(path),
            "name": path.stem,
            "description": content.get("description", "") or "",
            "node_count": len(content.get("nodes", [])),
            "component": _component_of(content),
            "opened_at": time.time(),
        }
        key = os.path.normcase(os.path.abspath(path))
        with self._recents_lock:
            kept = [r for r in self._recents() if os.path.normcase(os.path.abspath(r.get("path", ""))) != key]
            kept = [card, *kept][:MAX_RECENT]
            with contextlib.suppress(OSError):  # la liste des récents n'est jamais une raison d'échouer
                atomic.write_text(self._recents_file, json.dumps(kept, ensure_ascii=False, indent=1))

    # ---------------------------------------------------------------- lecture / écriture
    @staticmethod
    def _read(path: Path) -> tuple[dict[str, Any], bytes]:
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            raise NotFound(str(path)) from None
        except OSError as exc:
            raise FileAccessError(f"{path} : {exc.strerror or exc}") from exc
        try:
            content = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(content, dict):
                raise ValueError("un objet JSON est attendu")
            # complète les clés absentes d'un fichier écrit à la main ou par une version plus ancienne
            content = Flow.model_validate(content).model_dump(mode="json", by_alias=True)
        except (ValueError, UnicodeError) as exc:
            raise InvalidFlowFile(f"{path.name} n'est pas un flux lisible : {exc}") from exc
        return _named(path, content), raw

    @staticmethod
    def _dict(path: Path, content: dict[str, Any], raw: bytes, with_content: bool = True) -> dict[str, Any]:
        try:
            stat = path.stat()
            created, updated = stat.st_ctime, stat.st_mtime
        except OSError:
            created = updated = time.time()
        out: dict[str, Any] = {
            "id": make_id(path),
            "name": path.stem,
            "owner": "",
            "created_at": created,
            "updated_at": updated,
            "revision": _revision(raw),
            "component": _component_of(content),
            "path": str(path),
        }
        if with_content:
            out["flow"] = content
        return out

    def open_flow_file(self, path: str) -> dict[str, Any]:
        """Ouvre un fichier de flux et le range dans les récents."""
        target = Path(os.path.abspath(path))
        content, raw = self._read(target)
        self._remember(target, content)
        return self._dict(target, content, raw)

    def get_flow(self, flow_id: str) -> dict[str, Any]:
        path = flow_path(flow_id)
        content, raw = self._read(path)
        return self._dict(path, content, raw)

    def create_flow(self, name: str, content: dict[str, Any], owner: str, path: str | None = None) -> dict[str, Any]:
        if not path:
            raise ValueError("indiquer le fichier où enregistrer le flux")
        target = Path(os.path.abspath(path))
        if target.suffix.lower() != ".json":
            target = target.with_name(target.name + ".json")
        content = _named(target, content)
        try:
            atomic.write_text(target, _text(content), create_only=True)
        except FileExistsError:
            raise Conflict(f"{target.name} existe déjà : choisir un autre nom") from None
        except OSError as exc:
            raise FileAccessError(f"{target} : {exc.strerror or exc}") from exc
        self._remember(target, content)
        return self._dict(target, content, _text(content).encode("utf-8"))

    def update_flow(
        self, flow_id: str, name: str, content: dict[str, Any], revision: int | str | None
    ) -> dict[str, Any]:
        path = flow_path(flow_id)
        if not path.exists():
            raise NotFound(str(path))
        if revision is not None:
            _current, raw = self._read(path)
            if str(revision) != _revision(raw):
                raise Conflict("le fichier a changé sur le disque depuis son ouverture")
        content = _named(path, content)
        try:
            atomic.write_text(path, _text(content))
        except OSError as exc:
            raise FileAccessError(f"{path} : {exc.strerror or exc}") from exc
        self._remember(path, content)
        return self._dict(path, content, _text(content).encode("utf-8"))

    def list_flows(self) -> list[dict[str, Any]]:
        """Flux récents, le plus récent d'abord ; aucun accès disque (un partage injoignable ne bloque rien)."""
        out = []
        for r in self._recents():
            path = Path(r.get("path", ""))
            out.append(
                {
                    "id": make_id(path),
                    "name": path.stem,
                    "owner": "",
                    "created_at": r.get("opened_at", 0),
                    "updated_at": r.get("opened_at", 0),
                    "revision": None,
                    "component": r.get("component"),
                    "description": r.get("description", ""),
                    "node_count": r.get("node_count", 0),
                    "path": str(path),
                }
            )
        return out

    def delete_flow(self, flow_id: str) -> None:
        """Retire le flux des récents ; le fichier lui-même n'est jamais supprimé par l'application."""
        key = os.path.normcase(str(flow_path(flow_id)))
        with self._recents_lock:
            recents = self._recents()
            kept = [r for r in recents if os.path.normcase(os.path.abspath(r.get("path", ""))) != key]
            if len(kept) == len(recents):
                raise NotFound(flow_id)
            atomic.write_text(self._recents_file, json.dumps(kept, ensure_ascii=False, indent=1))

    # ---------------------------------------------------------------- composants
    @staticmethod
    def _component_dict(entry: complib.Entry, with_content: bool = True) -> dict[str, Any]:
        raw = entry.path.read_bytes()
        out = FileStore._dict(entry.path, json.loads(raw.decode("utf-8-sig")), raw, with_content=with_content)
        out["component"] = entry.name
        return out

    def list_components(self) -> list[dict[str, Any]]:
        found, _errors = complib.scan()
        return [self._component_dict(found[name]) for name in sorted(found)]

    def get_component(self, name: str) -> dict[str, Any] | None:
        entry = complib.find_entry(name)
        return self._component_dict(entry) if entry else None

    def publish_component(self, name: str, content: dict[str, Any], note: str, author: str) -> dict[str, Any]:
        try:
            return complib.publish(name, content, note, author)
        except complib.UnknownComponent:
            raise NotFound(name) from None

    def list_component_versions(self, name: str) -> list[dict[str, Any]]:
        return complib.list_versions(name)

    def get_component_version(self, name: str, version: int | None = None) -> dict[str, Any] | None:
        return complib.get_version(name, version)

    def latest_component_versions(self) -> dict[str, int]:
        return complib.latest_versions()
