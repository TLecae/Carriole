"""Dossiers de l'application et réglages de l'utilisateur.

Priorité : variable `ETL_*` > `settings.json` > valeur par défaut. Les variables d'environnement restent donc la
façon de configurer un futur mode serveur (Linux, cloud) ; le poste de bureau s'appuie sur les dossiers de
l'utilisateur et sur `settings.json`.

Dossiers (Windows) : données dans `%LOCALAPPDATA%\\Carriole` (base, cache, plugins : jamais sur un partage),
réglages dans `%APPDATA%\\Carriole`. `ETL_HOME` déplace les deux (tests, essais, installation portable). Les dossiers
de l'ancien nom (`AtelierETL`) sont renommés au premier lancement : flux récents, runs et réglages sont conservés.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

APP_NAME = "Carriole"
LEGACY_NAMES = {
    "Carriole": "AtelierETL",
    "carriole": "atelier-etl",
}  # ancien nom de l'application (avant le 2026-10-13)
SETTINGS_FILE = "settings.json"


@dataclass(frozen=True)
class AppDirs:
    data: Path  # base de métadonnées, cache, plugins, journaux : local à la machine
    config: Path  # settings.json

    @property
    def meta(self) -> Path:
        return self.data / "meta"

    @property
    def cache(self) -> Path:
        return self.data / "cache"

    @property
    def staging(self) -> Path:
        return self.data / "staging"

    @property
    def logs(self) -> Path:
        return self.data / "logs"

    @property
    def plugins(self) -> Path:
        return self.data / "plugins"

    @property
    def settings_file(self) -> Path:
        return self.config / SETTINGS_FILE


def app_dirs() -> AppDirs:
    home = os.environ.get("ETL_HOME", "").strip()
    if home:
        root = Path(home)
        return AppDirs(data=root / "data", config=root / "config")
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        roaming = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return AppDirs(data=_folder(Path(local), APP_NAME), config=_folder(Path(roaming), APP_NAME))
    data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    config = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return AppDirs(data=_folder(Path(data), "carriole"), config=_folder(Path(config), "carriole"))


def _folder(parent: Path, name: str) -> Path:
    """`parent/name` ; le dossier de l'ancien nom y est renommé s'il existe seul (même règle dans desktop/backend.ts).
    S'il ne peut pas l'être (fichiers ouverts par une ancienne version encore lancée), il reste utilisé tel quel."""
    folder, legacy = parent / name, parent / LEGACY_NAMES[name]
    if not folder.exists() and legacy.is_dir():
        try:
            legacy.rename(folder)
        except OSError:
            return legacy
    return folder


_cache: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}  # chemin -> ((mtime_ns, taille), contenu)
_lock = (
    threading.RLock()
)  # lectures et écritures de ce processus : sous Windows, remplacer un fichier en cours de lecture échoue
_RETRIES = 8  # un autre processus (worker) peut lire au même instant : on réessaie un court moment


def _retry(action):
    """Exécute `action` ; réessaie brièvement si Windows refuse l'accès (fichier ouvert ailleurs à cet instant)."""
    for attempt in range(_RETRIES):
        try:
            return action()
        except PermissionError:
            if attempt == _RETRIES - 1:
                raise
            time.sleep(0.02 * (attempt + 1))


def load() -> dict[str, Any]:
    """Contenu de `settings.json` ; vide si absent. Un fichier illisible est une erreur franche (pas ignoré).

    Relu seulement quand le fichier change : l'analyse d'un flux consulte les réglages à chaque frappe.
    """
    path = app_dirs().settings_file
    with _lock:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return {}
        signature = (stat.st_mtime_ns, stat.st_size)
        cached = _cache.get(str(path))
        if cached and cached[0] == signature:
            return copy.deepcopy(cached[1])
        try:
            data = json.loads(_retry(lambda: path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            raise ValueError(f"réglages illisibles ({path}) : {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"réglages illisibles ({path}) : un objet JSON est attendu")
        _cache[str(path)] = (signature, data)
        return copy.deepcopy(data)


def save(values: dict[str, Any]) -> None:
    """Écriture atomique : un plantage en cours d'écriture ne laisse pas un fichier tronqué."""
    path = app_dirs().settings_file
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".settings-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(values, handle, ensure_ascii=False, indent=2, sort_keys=True)
        with _lock:
            _retry(lambda: os.replace(tmp, path))
            _cache.pop(str(path), None)  # un écrit de même taille dans la même milliseconde ne doit pas rester caché
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def get(key: str, default: Any = None) -> Any:
    """Valeur d'un réglage ; `a.b` descend dans les objets imbriqués."""
    node: Any = load()
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def set_value(key: str, value: Any) -> None:
    with _lock:  # lecture, modification et écriture sans qu'un autre fil de ce processus s'intercale
        values = load()
        node = values
        parts = key.split(".")
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = node[part] = {}
            node = child
        node[parts[-1]] = value
        save(values)


def unset(key: str) -> bool:
    with _lock:
        values = load()
        node = values
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.get(part) if isinstance(node, dict) else None
            if not isinstance(node, dict):
                return False
        if parts[-1] not in node:
            return False
        del node[parts[-1]]
        save(values)
        return True


def language() -> str:
    """Langue de l'interface et des messages : `ETL_LANG`, sinon réglage `language`, sinon français."""
    return (os.environ.get("ETL_LANG") or str(get("language", "") or "") or "fr").strip().lower()[:2]


def apply_environment() -> None:
    """Réglages qui passent par l'environnement du processus (proxy, budget mémoire), pour le backend, ses workers
    (qui héritent de l'environnement) et la ligne de commande. Une variable déjà définie l'emporte."""
    proxy = str(get("proxy", "") or "").strip()
    if proxy:
        for name in ("HTTPS_PROXY", "HTTP_PROXY"):
            os.environ.setdefault(name, proxy)
    budget = get("memory_budget_mb")
    if isinstance(budget, int) and not isinstance(budget, bool) and budget > 0:
        os.environ.setdefault("ETL_MEMORY_BUDGET_MB", str(budget))
    cache = str(get("cache_dir", "") or "").strip()  # résultats des runs et débordement des calculs : un disque local
    if cache:
        os.environ.setdefault("ETL_CACHE_DIR", cache)
