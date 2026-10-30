"""DuckDB au même endroit pour le moteur, l'inspecteur et les tuiles : extension spatiale et ressources.

L'installeur préinstalle l'extension dans le dossier du programme (`ETL_DUCKDB_EXTENSIONS`) : aucun téléchargement,
le poste n'a pas besoin d'accès à internet. Sans ce dossier (développement), DuckDB la télécharge au premier usage.

Ressources : sans réglage, chaque connexion DuckDB s'autorise 80 % de la mémoire du poste et tous ses cœurs. Un run
ouvre beaucoup de connexions à la fois (une par lot en cours, une par nœud bloquant…) et plusieurs runs tournent
ensemble : `connect` donne à chacune sa part du budget du run (`configure_run`, sinon l'environnement).
"""

from __future__ import annotations

import contextlib
import errno
import os
import re
import shutil
import tempfile
import threading
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

import duckdb
import psutil
import pyarrow.parquet as pq

_lock = threading.Lock()
_available: bool | None = None  # None = pas encore tenté

# Ressources du run en cours dans ce processus (un worker exécute un run à la fois) : voir `configure_run`.
_run: dict[str, object] = {}

Kind = Literal["expr", "heavy", "inspect", "view"]


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
    _run.pop("heavy_nodes", None)


def configure_heavy_nodes(count: int) -> None:
    """Nombre de nœuds lourds (voie SQL, bloquants) à exécuter dans le run : ils se partagent la part de DuckDB, sans
    la diviser par plus de nœuds qu'il n'y en a (un nœud lourd seul la reçoit entière)."""
    _run["heavy_nodes"] = max(1, count)


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


def heavy_share() -> int:
    """Nœuds lourds entre lesquels la part de DuckDB est divisée : `heavy_slots`, au plus le nombre de ceux du run."""
    return max(1, min(heavy_slots(), int(_run.get("heavy_nodes") or heavy_slots())))  # type: ignore[call-overload]


DUCK_SHARE = 0.6  # part du budget du run donnée à DuckDB (`memory_limit`) ; le reste va aux lots Arrow de Python


def batch_workers(threads: int | None = None) -> int:
    """Lots traités en parallèle par nœud : `ETL_BATCH_WORKERS`, sinon la moitié des cœurs du run (2 à 8)."""
    env = os.environ.get("ETL_BATCH_WORKERS")
    if env:
        return max(1, int(env))
    return max(2, min(8, (threads or run_threads()) // 2))


def batch_bytes() -> int:
    """Taille d'un lot (données décompressées) : la part Arrow du budget du run, partagée entre les lots en cours
    (`batch_workers` par nœud, deux nœuds à la fois) et leurs copies (entrée, résultat, file d'attente)."""
    arrow = run_memory_mb() * (1 - DUCK_SHARE) * 2**20
    return int(max(16 * 2**20, arrow / (2 * batch_workers() * 4)))


def spill_dir() -> Path:
    """Dossier de débordement sur disque : celui du run, sinon un dossier temporaire du processus."""
    folder = _run.get("spill_dir") or Path(tempfile.gettempdir()) / f"etl-duck-{os.getpid()}"
    return Path(folder)  # type: ignore[arg-type]


def limits(kind: Kind) -> tuple[int, int]:
    """(mémoire en Mo, threads) d'une connexion :

    - `expr` : expressions sur un lot (une connexion par lot en cours) : un thread, une petite part ;
    - `heavy` : nœud bloquant ou voie SQL : la part de DuckDB (`DUCK_SHARE`) du budget du run, partagée entre les
      nœuds lourds qui peuvent tourner ensemble (`heavy_share`) ;
    - `inspect` : requêtes de l'inspecteur (processus de l'API) : un quart de la machine ;
    - `view` : préparation de la carte d'un port, que l'utilisateur attend : la mémoire de l'inspecteur, tous les
      cœurs.
    """
    if kind == "inspect":
        return max(256, machine_budget_mb() // 4), max(1, machine_threads() // 2)
    if kind == "view":
        return max(256, machine_budget_mb() // 4), machine_threads()
    memory, threads = run_memory_mb(), run_threads()
    if kind == "expr":
        return max(256, int(memory * DUCK_SHARE / max(2, threads))), 1
    return max(256, int(memory * DUCK_SHARE / heavy_share())), threads


def apply_limits(con: duckdb.DuckDBPyConnection, kind: Kind, spill: Path | None = None) -> None:
    """Applique `limits(kind)` et le dossier de débordement à `con` (avant `lock_configuration`)."""
    memory, threads = limits(kind)
    con.execute(f"SET memory_limit='{memory}MB'")
    con.execute(f"SET threads={int(os.environ.get('ETL_DUCK_THREADS') or threads)}")
    # un sous-dossier par connexion : DuckDB le crée au premier débordement et le supprime à la fermeture
    parent = spill or spill_dir()
    parent.mkdir(parents=True, exist_ok=True)
    con.execute("SET temp_directory=?", [(parent / uuid.uuid4().hex[:12]).as_posix()])


READ_FACTOR = 4  # mémoire d'un thread qui lit un bloc Parquet : environ 4 fois sa taille décompressée (mesuré)
WRITE_BLOCK_BYTES = 32 * 2**20  # bloc écrit par `COPY` : au plus cette taille décompressée (lignes larges)


def fit(con: duckdb.DuckDBPyConnection, files: list[Path]) -> int:
    """Règle la connexion sur la largeur des données qu'elle lit (métadonnées Parquet de `files`) ; renvoie le nombre de
    lignes d'un bloc écrit par `COPY` (`ROW_GROUP_SIZE`). Sur des lignes larges (200 colonnes de texte), chaque thread
    garde un bloc lu décompressé, et `COPY` un bloc de 122 880 lignes par colonne : au-delà de la mémoire, DuckDB
    déborde sur disque et devient dix fois plus lent. Avant `confine` (configuration verrouillée ensuite)."""
    rows = size = block = 0
    for f in files:
        meta = pq.ParquetFile(f).metadata
        rows += meta.num_rows
        for i in range(meta.num_row_groups):
            group = meta.row_group(i).total_byte_size
            size += group
            block = max(block, group)
    if not rows or not size:
        return DEFAULT_ROW_GROUP
    memory_mb, threads = con.execute("SELECT current_setting('memory_limit'), current_setting('threads')").fetchall()[0]
    memory = _bytes(str(memory_mb))
    fitted = max(1, min(int(threads), memory // max(1, READ_FACTOR * block)))
    con.execute(f"SET threads={fitted}")
    return int(max(2048, min(DEFAULT_ROW_GROUP, WRITE_BLOCK_BYTES * rows // size)))


DEFAULT_ROW_GROUP = 122_880  # taille d'un bloc écrit par DuckDB, par défaut


def _bytes(setting: str) -> int:
    """Taille lue dans un réglage de DuckDB (« 921.0 MiB », « 3.5 GiB »…)."""
    number, _, unit = setting.strip().partition(" ")
    scale = {"B": 1, "KiB": 2**10, "MiB": 2**20, "GiB": 2**30, "TiB": 2**40, "KB": 10**3, "MB": 10**6, "GB": 10**9}
    return int(float(number) * scale.get(unit.strip(), 1))


def confine(con: duckdb.DuckDBPyConnection, directories: list[Path], files: list[Path]) -> None:
    """Après le chargement des extensions : la connexion ne lit et n'écrit que dans `directories` et ne lit en plus que
    `files`. Une expression d'utilisateur n'y atteint rien d'autre (comme dans `expr`), même composée dans une requête
    qui lit les ports (ADR 0044). Le débordement sur disque reste permis."""

    def listing(paths: list[Path]) -> str:
        quoted = ["'" + p.as_posix().replace("'", "''") + "'" for p in dict.fromkeys(paths)]
        return "[" + ", ".join(quoted) + "]"

    temp = con.execute("SELECT current_setting('temp_directory')").fetchall()[0][0]
    dirs = [*directories, *([Path(temp)] if temp else [])]
    con.execute(f"SET allowed_directories={listing(dirs)}")
    if files:
        con.execute(f"SET allowed_paths={listing(files)}")
    con.execute("SET enable_external_access=false")
    con.execute("SET lock_configuration=true")


def keep_results(con: duckdb.DuckDBPyConnection) -> None:
    """Les expressions des flux donnent les mêmes résultats qu'avec DuckDB 1.5 (avant `lock_configuration`) :
    `NULL` seul reste une colonne typée (les écrivains n'ont pas de colonne sans type), `//` et `%` par zéro donnent
    NULL au lieu d'une erreur, et `~` compare toute la valeur (pas une partie)."""
    con.execute("SET legacy_disable_null_type = true")
    con.execute("SET error_on_division_by_zero = false")
    con.execute("SET regex_match_operator_semantics = 'full'")


def connect(kind: Kind, spill: Path | None = None) -> duckdb.DuckDBPyConnection:
    """Connexion DuckDB en mémoire, bornée selon `kind` (voir `limits`)."""
    con = duckdb.connect()
    apply_limits(con, kind, spill)
    keep_results(con)
    return con


class DiskFull(OSError):
    """Plus de place sur le disque : débordement de DuckDB, fichier d'un port… Pas une erreur des données."""


_NO_SPACE = ("no space left", "not enough space", "espace insuffisant", "disk is full")
_WRITTEN = re.compile(r'"((?:[A-Za-z]:)?[/\\][^"]+)"')  # le fichier que DuckDB n'a pas pu écrire


def resource_error(exc: BaseException) -> MemoryError | DiskFull | None:
    """`exc` traduite pour l'utilisateur quand la machine manque de mémoire (DuckDB) ou de place sur le disque (DuckDB,
    Arrow, Python) ; None pour toute autre erreur. Les deux arrêtent le nœud : ni rejet d'entités, ni repli sur la voie
    par lots, qui manquerait des mêmes ressources."""
    if isinstance(exc, (MemoryError, DiskFull)):
        return exc
    if isinstance(exc, duckdb.OutOfMemoryException):
        return MemoryError(str(exc).splitlines()[0])
    text = str(exc)
    if getattr(exc, "errno", None) != errno.ENOSPC and not any(k in text.lower() for k in _NO_SPACE):
        return None
    found = _WRITTEN.search(text)
    place = Path(found.group(1)) if found else spill_dir()
    try:
        free = f" ({shutil.disk_usage(place.anchor or place).free / 2**30:.1f} Go libres)"
    except OSError:
        free = ""
    return DiskFull(
        f"espace disque insuffisant sur {place.anchor or place}{free} : les calculs qui dépassent le budget mémoire "
        "y écrivent leurs données temporaires, dans le dossier du cache. Libérer de la place, filtrer les données en "
        "amont, augmenter le budget mémoire ou choisir un autre dossier du cache dans Paramètres"
    )


@contextlib.contextmanager
def interrupted_when(con: duckdb.DuckDBPyConnection, stop: Callable[[], bool] | None) -> Iterator[None]:
    """Interrompt la requête en cours sur `con` dès que `stop()` est vrai (run annulé, préparation que plus personne
    n'attend) : elle lève `duckdb.InterruptException`."""
    done = threading.Event()

    def watch() -> None:
        while not done.wait(0.2):
            if stop is not None and stop():
                con.interrupt()  # tant que dure le bloc : une requête qui démarre juste après l'est aussi

    if stop is not None:
        threading.Thread(target=watch, daemon=True).start()
    try:
        yield
    finally:
        done.set()
