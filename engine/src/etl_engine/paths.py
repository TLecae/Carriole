"""Dossiers autorisés pour lire et écrire des fichiers.

Un flux est du code : sans garde-fou, n'importe quel utilisateur d'un serveur partagé pourrait lire `/etc/passwd` ou
écraser un fichier système depuis un nœud « Lecture de fichier ». Quand `ETL_READ_ROOTS` / `ETL_WRITE_ROOTS`
(liste de dossiers séparés par `:` ou `;` sous Windows) sont définis, les modificateurs n'accèdent qu'à ces dossiers ;
liens symboliques et `..` sont résolus avant le contrôle. Non définis (poste de bureau, ligne de commande) : pas de
restriction, l'utilisateur a les droits de sa session.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from etl_engine.sdk import ConfigError


def roots(mode: Literal["read", "write"]) -> list[Path] | None:
    raw = os.environ.get("ETL_READ_ROOTS" if mode == "read" else "ETL_WRITE_ROOTS", "").strip()
    if not raw:
        return None
    return [Path(p).resolve() for p in raw.split(os.pathsep) if p.strip()]


def check(path: str | os.PathLike[str], mode: Literal["read", "write"]) -> str:
    """Chemin s'il est dans un dossier autorisé ; sinon `ConfigError` en français."""
    allowed = roots(mode)
    resolved = Path(path).resolve()
    if allowed is None or any(resolved == root or root in resolved.parents for root in allowed):
        return str(path)
    action = "lire" if mode == "read" else "écrire"
    listing = ", ".join(str(r) for r in allowed)
    raise ConfigError(f"accès refusé : « {path} » n'est pas dans un dossier où l'on peut {action} ({listing})")


# --- explorateur de fichiers (API) --------------------------------------------------------------------------

MAX_ENTRIES = 5000


def _labels() -> dict[Path, str]:
    """`ETL_ROOT_LABELS` : « chemin=libellé » séparés par `|` (ex. `/data/sources=Sources (T:)`)."""
    out: dict[Path, str] = {}
    for item in os.environ.get("ETL_ROOT_LABELS", "").split("|"):
        path, sep, label = item.partition("=")
        if sep and path.strip() and label.strip():
            out[Path(path.strip()).resolve()] = label.strip()
    return out


def _windows_roots() -> list[dict[str, Any]]:
    """Poste de bureau sans restriction : dossier personnel, puis les lecteurs (réseau signalés, jamais sondés :
    un lecteur réseau injoignable ferait attendre l'explorateur)."""
    import ctypes

    out = [{"path": str(Path.home()), "label": "Dossier personnel", "available": True, "reason": None}]
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for i in range(26):
        if mask & (1 << i):
            root = f"{chr(65 + i)}:\\"
            kind = ctypes.windll.kernel32.GetDriveTypeW(root)  # 4 = lecteur réseau
            label = f"{root[:2]} (réseau)" if kind == 4 else f"{root[:2]}"
            out.append({"path": root, "label": label, "available": True, "reason": None})
    return out


def browse_roots(mode: Literal["read", "write"]) -> list[dict[str, Any]]:
    """Points d'entrée de l'explorateur : les dossiers autorisés (ou, sans restriction, les dossiers de données)."""
    allowed = roots(mode)
    if allowed is None and os.name == "nt":
        return _windows_roots()
    if allowed is None:
        env = ("ETL_DATA_OUT", "ETL_DATA_IN") if mode == "write" else ("ETL_DATA_IN", "ETL_DATA_OUT")
        allowed = [Path(os.environ[v]).resolve() for v in env if os.environ.get(v)] or [Path.cwd()]
    labels = _labels()
    out = []
    for r in allowed:
        reason = None if r.is_dir() else "dossier introuvable"
        out.append({"path": str(r), "label": labels.get(r, str(r)), "available": reason is None, "reason": reason})
    return out


def _inside(resolved: Path, allowed: list[Path] | None) -> bool:
    return allowed is None or any(resolved == r or r in resolved.parents for r in allowed)


def listing(path: str, mode: Literal["read", "write"], extensions: tuple[str, ...] = ()) -> dict[str, Any]:
    """Contenu d'un dossier autorisé : sous-dossiers d'abord, puis fichiers (filtrés par extension si demandé).

    Les liens qui sortent des dossiers autorisés sont omis. `parent` vaut `None` à la racine d'un dossier autorisé :
    l'explorateur remonte alors à la liste des racines.
    """
    allowed = roots(mode)
    resolved = Path(check(path, mode)).resolve()
    if not resolved.is_dir():
        raise ConfigError(f"« {path} » n'est pas un dossier")
    entries: list[dict[str, Any]] = []
    truncated = False
    try:
        children = sorted(os.scandir(resolved), key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError as exc:
        raise ConfigError(f"dossier illisible : {exc.strerror or exc}") from None
    for entry in children:
        if entry.name.startswith(".") or entry.name.startswith("$"):
            continue
        try:
            target = Path(entry.path).resolve()
            if not _inside(target, allowed):
                continue
            is_dir = entry.is_dir()
            if not is_dir and extensions and target.suffix.lower() not in extensions:
                continue
            stat = entry.stat()
        except OSError:
            continue  # lien cassé, fichier verrouillé : on ne l'affiche pas
        if len(entries) >= MAX_ENTRIES:
            truncated = True
            break
        entries.append(
            {
                "name": entry.name,
                "path": str(Path(entry.path)),
                "kind": "dir" if is_dir else "file",
                "size": None if is_dir else stat.st_size,
                "modified": stat.st_mtime,
            }
        )
    parent = resolved.parent
    is_root = allowed is not None and resolved in allowed
    return {
        "path": str(resolved),
        "parent": None if is_root or parent == resolved or not _inside(parent, allowed) else str(parent),
        "entries": entries,
        "truncated": truncated,
    }
