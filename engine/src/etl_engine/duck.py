"""DuckDB au même endroit pour le moteur, l'inspecteur et les tuiles : extension spatiale et ressources.

L'installeur préinstalle l'extension dans le dossier du programme (`ETL_DUCKDB_EXTENSIONS`) : aucun téléchargement,
le poste n'a pas besoin d'accès à internet. Sans ce dossier (développement), DuckDB la télécharge au premier usage.

Ressources : sans réglage, chaque connexion DuckDB s'autorise 80 % de la mémoire du poste et tous ses cœurs. Un run
ouvre beaucoup de connexions à la fois (une par lot en cours, une par nœud bloquant…) et plusieurs runs tournent
ensemble : `connect` donne à chacune sa part du budget du run (`configure_run`, sinon l'environnement).
"""

from __future__ import annotations

import os
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

import duckdb
import psutil

_lock = threading.Lock()
_available: bool | None = None  # None = pas encore tenté

# Ressources du run en cours dans ce processus (un worker exécute un run à la fois) : voir `configure_run`.
_run: dict[str, object] = {}

Kind = Literal["expr", "heavy", "inspect"]


def _extension_dir() -> str | None:
    return os.environ.get("ETL_DUCKDB_EXTENSIONS") or None


def configure(con: duckdb.DuckDBPyConnection) -> None:
    """À appeler avant `lock_configuration` : fixe le dossier des extensions préinstallées."""
    folder = _extension_dir()
    if folder:
        con.execute("SET extension_directory = ?", [folder])


def load_spatial(con: duckdb.DuckDBPyConnection) -> None:
    """Charge l'extension spatiale (préinstallée si possible, sinon installée puis chargée). Lève `duckdb.Error`."""
    configure(con)
    try:
        con.execute("LOAD spatial")
    except duckdb.Error:
        con.execute("INSTALL spatial")
        con.execute("LOAD spatial")
    # La géométrie d'un fichier GeoParquet (métadonnées « geo », gardées par un port qui le lit) reste du WKB, comme
    # partout ailleurs : convertie en GEOMETRY, elle cassait ST_GeomFromWKB et la lecture de l'en-tête WKB.
    con.execute("SET enable_geoparquet_conversion = false")


def load_httpfs(con: duckdb.DuckDBPyConnection) -> None:
    """Charge l'extension de lecture sur le web (http, https), préinstallée comme l'extension spatiale, avec le proxy
    des paramètres (`HTTPS_PROXY`, voir `settings.apply_environment`). Lève `duckdb.Error`."""
    configure(con)
    try:
        con.execute("LOAD httpfs")
    except duckdb.Error:
        con.execute("INSTALL httpfs")
        con.execute("LOAD httpfs")
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or os.environ.get("HTTP_PROXY")
    if proxy:
        found = urlsplit(proxy if "://" in proxy else f"http://{proxy}")
        con.execute("SET http_proxy = ?", [f"{found.hostname}:{found.port or 80}"])
        if found.username:
            con.execute("SET http_proxy_username = ?", [unquote(found.username)])
            con.execute("SET http_proxy_password = ?", [unquote(found.password or "")])


def spatial_available() -> bool:
    """Vrai si l'extension se charge ; l'installation éventuelle n'est tentée qu'une fois par processus."""
    global _available
    with _lock:
        if _available is None:
            try:
                load_spatial(duckdb.connect())
                _available = True
            except duckdb.Error:
                _available = False
        return _available


# ------------------------------------------------------------------------------------------ ressources


def machine_budget_mb() -> int:
    """Mémoire que les runs peuvent utiliser : `ETL_MEMORY_BUDGET_MB` (Paramètres), sinon 75 % du poste."""
    env = os.environ.get("ETL_MEMORY_BUDGET_MB")
    if env:
        return max(512, int(env))
    return max(512, int(psutil.virtual_memory().total / 2**20 * 0.75))


def machine_threads() -> int:
    return max(1, psutil.cpu_count() or 1)


def configure_run(memory_mb: int | None = None, threads: int | None = None, spill_dir: Path | None = None) -> None:
    """Fixe les ressources du run qui démarre dans ce processus (sa part de la machine quand plusieurs runs tournent).

    `None` : le budget de la machine (`ETL_RUN_MEMORY_MB` / `ETL_RUN_THREADS` si le worker les a posés).
    """
    env_mem, env_threads = os.environ.get("ETL_RUN_MEMORY_MB"), os.environ.get("ETL_RUN_THREADS")
    _run["memory_mb"] = max(512, memory_mb or (int(env_mem) if env_mem else machine_budget_mb()))
    _run["threads"] = max(1, threads or (int(env_threads) if env_threads else machine_threads()))
    _run["spill_dir"] = spill_dir


def run_memory_mb() -> int:
    if "memory_mb" not in _run:
        configure_run()
    return int(_run["memory_mb"])  # type: ignore[arg-type]


def run_threads() -> int:
    if "threads" not in _run:
        configure_run()
    return int(_run["threads"])  # type: ignore[arg-type]


def heavy_slots() -> int:
    """Nœuds lourds (bloquants en Python, voie SQL) exécutés en même temps dans un run : `ETL_BLOCKING_NODES`, 2."""
    return max(1, int(os.environ.get("ETL_BLOCKING_NODES", "2")))


def spill_dir() -> Path:
    """Dossier de débordement sur disque : celui du run, sinon un dossier temporaire du processus."""
    folder = _run.get("spill_dir") or Path(tempfile.gettempdir()) / f"etl-duck-{os.getpid()}"
    return Path(folder)  # type: ignore[arg-type]


def limits(kind: Kind) -> tuple[int, int]:
    """(mémoire en Mo, threads) d'une connexion :

    - `expr` : expressions sur un lot (une connexion par lot en cours) : un thread, une petite part ;
    - `heavy` : nœud bloquant ou voie SQL : 60 % du budget du run partagés entre les nœuds lourds simultanés ;
    - `inspect` : requêtes de l'inspecteur (processus de l'API) : un quart de la machine.
    """
    if kind == "inspect":
        return max(256, machine_budget_mb() // 4), max(1, machine_threads() // 2)
    memory, threads = run_memory_mb(), run_threads()
    if kind == "expr":
        return max(256, int(memory * 0.6 / max(2, threads))), 1
    return max(256, int(memory * 0.6 / heavy_slots())), threads


def apply_limits(con: duckdb.DuckDBPyConnection, kind: Kind, spill: Path | None = None) -> None:
    """Applique `limits(kind)` et le dossier de débordement à `con` (avant `lock_configuration`)."""
    memory, threads = limits(kind)
    con.execute(f"SET memory_limit='{memory}MB'")
    con.execute(f"SET threads={int(os.environ.get('ETL_DUCK_THREADS') or threads)}")
    # un sous-dossier par connexion : DuckDB le crée au premier débordement et le supprime à la fermeture
    parent = spill or spill_dir()
    parent.mkdir(parents=True, exist_ok=True)
    con.execute("SET temp_directory=?", [(parent / uuid.uuid4().hex[:12]).as_posix()])


def connect(kind: Kind, spill: Path | None = None) -> duckdb.DuckDBPyConnection:
    """Connexion DuckDB en mémoire, bornée selon `kind` (voir `limits`)."""
    con = duckdb.connect()
    apply_limits(con, kind, spill)
    return con
