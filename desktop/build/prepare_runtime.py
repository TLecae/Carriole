"""Prépare le Python embarqué : extension spatiale de DuckDB, grilles PROJ françaises, bytecode.

Lancé PAR le Python embarqué (voir scripts/build_desktop.ps1) : `python prepare_runtime.py <dossier runtime>`.
Ce qui est téléchargé ici ne l'est donc jamais chez l'utilisateur : le poste n'a pas besoin d'internet pour fonctionner.
"""

from __future__ import annotations

import compileall
import subprocess
import sys
import sysconfig
from pathlib import Path


def main(runtime: Path) -> None:
    import duckdb

    ext = runtime / "duckdb_ext"
    ext.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET extension_directory = ?", [str(ext)])
    con.execute("INSTALL spatial")
    # contrôle : l'extension se charge depuis ce dossier, sans réseau
    check = duckdb.connect()
    check.execute("SET extension_directory = ?", [str(ext)])
    check.execute("LOAD spatial")
    print("extension spatiale DuckDB :", sorted(p.name for p in ext.rglob("*.duckdb_extension")))

    proj = runtime / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "pyproj", "sync", "--source-id", "fr_ign", "--target-directory", str(proj)], check=True
    )
    print("grilles PROJ :", len(list(proj.glob("*"))), "fichier(s)")

    # installé dans C:\Program Files ou dans une AppImage (lecture seule pour l'utilisateur) :
    # le bytecode est livré, jamais écrit à l'exécution (bibliothèque standard et site-packages)
    compileall.compile_dir(sysconfig.get_paths()["stdlib"], quiet=1, workers=0)


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
