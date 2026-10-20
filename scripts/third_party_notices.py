"""Écrit THIRD_PARTY_NOTICES.md : composants tiers livrés dans l'installeur (paquets Python, interface).

À relancer après un changement de dépendances (uv.lock, frontend/package-lock.json) :
    .venv\\Scripts\\python.exe scripts\\third_party_notices.py
Lit l'environnement `.venv` (uv sync) et `frontend/node_modules` (npm ci).
"""

from __future__ import annotations

import importlib.metadata as md
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/TLecae/Carriole"

NOTES = {
    "psycopg": "LGPL : bibliothèque non modifiée, source sur https://github.com/psycopg/psycopg",
    "psycopg-binary": "LGPL : bibliothèque non modifiée, source sur https://github.com/psycopg/psycopg",
}


def python_packages() -> list[tuple[str, str, str]]:
    """(nom, version, licence) des dépendances livrées : celles du verrou, sans les outils de développement."""
    export = subprocess.run(
        ["uv", "export", "--frozen", "--no-dev", "--no-hashes", "--no-header", "--no-emit-workspace"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    rows = []
    for line in export.splitlines():
        match = re.match(r"([A-Za-z0-9_.-]+)==([^ ;]+)", line)
        if not match:
            continue
        name, version = match.groups()
        try:
            meta = md.metadata(name)
        except md.PackageNotFoundError:
            rows.append((name, version, "voir PyPI (paquet propre à une autre plateforme)"))
            continue
        rows.append((name, version, _license(meta)))
    return sorted(rows, key=lambda r: r[0].lower())


def _license(meta) -> str:
    if meta.get("License-Expression"):
        return meta["License-Expression"]
    classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    if classifiers:
        return ", ".join(classifiers)
    text = (meta.get("License") or "").strip()
    return text.splitlines()[0][:80] if text and len(text) < 200 else "voir le paquet"


def node_packages(folder: Path) -> list[tuple[str, str, str]]:
    """Paquets de production installés (dépendances transitives comprises) d'après package-lock.json."""
    lock = json.loads((folder / "package-lock.json").read_text(encoding="utf-8"))
    rows = {}
    for key, info in lock.get("packages", {}).items():
        if not key.startswith("node_modules/") or info.get("dev") or info.get("devOptional"):
            continue
        name = key.split("node_modules/")[-1]
        rows[name] = (name, info.get("version", "?"), info.get("license", "voir le paquet"))
    return sorted(rows.values(), key=lambda r: r[0].lower())


def table(rows: list[tuple[str, str, str]]) -> str:
    lines = ["| Composant | Version | Licence |", "| --- | --- | --- |"]
    for name, version, lic in rows:
        note = f" — {NOTES[name]}" if name in NOTES else ""
        lines.append(f"| {name} | {version} | {lic}{note} |")
    return "\n".join(lines)


def main() -> None:
    text = f"""# Composants tiers

Carriole est distribué sous licence MIT ([LICENSE](LICENSE)). L'installeur Windows et l'AppImage Linux embarquent
les composants ci-dessous, chacun sous sa propre licence (texte complet dans chaque paquet, dossier
`resources/runtime/python` de l'installation). Le code source de Carriole : {REPOSITORY}.

Également livrés : l'interpréteur **Python** (licence PSF), **Electron** et **Chromium** (MIT et licences listées
dans `LICENSES.chromium.html` à côté de l'exécutable), l'extension **spatial** de DuckDB (MIT ; GDAL, GEOS, PROJ)
et les grilles de transformation de l'**IGN** (Licence Ouverte Etalab 2.0).

## Python

{table(python_packages())}

## Interface (navigateur)

{table(node_packages(ROOT / "frontend"))}
"""
    (ROOT / "THIRD_PARTY_NOTICES.md").write_bytes(text.encode("utf-8"))
    print("THIRD_PARTY_NOTICES.md écrit", file=sys.stderr)


if __name__ == "__main__":
    main()
