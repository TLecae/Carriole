"""Installation de bureau : retrouver les ressources livrées (interface, extension DuckDB, grilles PROJ).

Disposition posée par l'installeur (dossier `resources` d'Electron) :

    resources/install.json        marqueur (version)
    resources/runtime/python/     Python embarqué (ce programme)
    resources/runtime/duckdb_ext/ extension spatiale de DuckDB, préinstallée (aucun téléchargement)
    resources/runtime/proj/       grilles de transformation françaises (fr_ign)
    resources/frontend/           interface compilée

Hors installation (développement, tests, serveur), rien n'est fait : les variables `ETL_*` s'appliquent comme avant.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def install_root() -> Path | None:
    """Dossier `resources` de l'installation si ce Python est celui qui y est embarqué."""
    for parent in Path(sys.executable).parents[:4]:
        if (parent / "install.json").is_file():
            return parent
    return None


def apply_env() -> Path | None:
    """Renseigne (sans écraser) les variables qui désignent les ressources livrées. Renvoie la racine, ou None."""
    root = install_root()
    if root is None:
        return None
    candidates = {
        "ETL_FRONTEND_DIR": root / "frontend",
        "ETL_DUCKDB_EXTENSIONS": root / "runtime" / "duckdb_ext",
        "PROJ_USER_WRITABLE_DIRECTORY": root / "runtime" / "proj",
    }
    for name, path in candidates.items():
        if path.is_dir():
            os.environ.setdefault(name, str(path))
    return root
