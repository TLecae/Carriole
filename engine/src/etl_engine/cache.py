"""Stockage des ports : un GeoParquet par port de sortie de chaque nœud (voir ADR 0003 et 0010).

Disposition : `<run>/<nœud>/<port>.parquet` et `<run>/<nœud>/meta.json` (empreinte, comptes, statut). Un port a
l'une de trois formes :

- `<port>.parquet` : son fichier ;
- `<port>.ref.json` : un fichier déjà au format des ports (Parquet lu tel quel), désigné au lieu d'être recopié
  (ADR 0043) ;
- `<port>.sql.json` : sa définition, une requête DuckDB sur les ports de l'amont, calculée quand on la lit (ADR 0044).

`port_data` donne le fichier d'un port qui en a un ; `port_relation`, la requête qui lit n'importe quel port.

Un consommateur « branché en direct » (voir ADR 0013) reçoit aussi les lots par une file bornée (`Tap`), au fur et
à mesure qu'ils sont écrits : il travaille en même temps que son amont au lieu d'attendre puis de relire le fichier.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from etl_engine import duck
from etl_engine.data import conform, get_crs, with_crs
from etl_engine.sdk import Cancelled, ConfigError

PORT_COMPRESSION = os.environ.get("ETL_PORT_COMPRESSION", "zstd")
ROW_GROUP_BYTES = 128 * 2**20  # un bloc Parquet ne dépasse pas ça (sauf une seule ligne plus grosse) : relu par blocs
RASTER = "raster"  # colonne des ports raster (`raster.core.RASTER`, sans charger rasterio ici)


def port_path(run_dir: Path, node_id: str, port: str) -> Path:
    return run_dir / node_id / f"{port}.parquet"


def ref_path(port_file: Path) -> Path:
    """Référence d'un port qui désigne un fichier existant au lieu d'avoir le sien."""
    return port_file.with_name(port_file.name.removesuffix(".parquet") + ".ref.json")


class SourceChanged(OSError):
    """Le fichier désigné par un port a changé (ou disparu) depuis le run : ses données ne sont plus celles du port."""


def refer(port_file: Path, source: str | Path) -> int:
    """Le port désigne `source` (déjà au format des ports) au lieu d'en recopier les lignes ; son nombre de lignes.
    Taille et date du fichier sont notées : modifié ensuite, il ne passe plus pour le port (`SourceChanged`)."""
    source = Path(os.path.abspath(source))
    st = source.stat()
    rows = pq.ParquetFile(source).metadata.num_rows
    port_file.parent.mkdir(parents=True, exist_ok=True)
    port_file.unlink(missing_ok=True)
    ref = {"path": str(source), "size": st.st_size, "mtime_ns": st.st_mtime_ns}
    ref_path(port_file).write_text(json.dumps(ref, ensure_ascii=False), encoding="utf-8")
    return rows


def port_data(port_file: Path) -> Path | None:
    """Fichier des données d'un port : le sien, ou celui qu'il désigne ; None : pas de données (port vide, cache
    purgé). Lève `SourceChanged` si le fichier désigné a changé depuis le run."""
    if port_file.is_file():
        return port_file
    try:
        ref = json.loads(ref_path(port_file).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    source = Path(ref["path"])
    try:
        st = source.stat()
    except OSError:
        raise SourceChanged(f"fichier lu introuvable : {source} ; relancer le flux") from None
    if (st.st_size, st.st_mtime_ns) != (ref["size"], ref["mtime_ns"]):
        raise SourceChanged(f"fichier modifié depuis sa lecture : {source} ; relancer le flux")
    return source


def sql_path(port_file: Path) -> Path:
    """Définition d'un port calculé à la demande (ADR 0044)."""
    return port_file.with_name(port_file.name.removesuffix(".parquet") + ".sql.json")


def _forms(port_file: Path) -> tuple[Path, Path, Path]:
    return port_file, ref_path(port_file), sql_path(port_file)


def has_port(port_file: Path) -> bool:
    """Le port a des données dans ce run (son fichier, sa référence ou sa définition)."""
    return any(f.is_file() for f in _forms(port_file))


def define(port_file: Path, query: str, inputs: dict[str, list[tuple[str, str]]], crs: str | None) -> None:
    """Le port est défini par `query` sur les relations `inputs` (nom -> ports de l'amont, réunis par nom de colonne) :
    rien n'est écrit, il est calculé quand on le lit (ADR 0044)."""
    port_file.parent.mkdir(parents=True, exist_ok=True)
    for f in _forms(port_file):
        f.unlink(missing_ok=True)
    spec = {"query": query, "inputs": {k: [list(p) for p in v] for k, v in inputs.items()}, "crs": crs}
    sql_path(port_file).write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")


_SPATIAL = re.compile(r"\bST_", re.IGNORECASE)


@dataclass
class Relation:
    """Requête DuckDB qui lit un port, quelle que soit sa forme ; `files` : les fichiers qu'elle lit."""

    sql: str
    crs: str | None = None
    files: list[Path] = field(default_factory=list)

    def needs_spatial(self) -> bool:
        return needs_spatial(self.sql)


def needs_spatial(sql: str) -> bool:
    """La requête se sert de fonctions de l'extension spatiale (`ST_…`)."""
    return bool(_SPATIAL.search(sql))


def sql_text(value: str | Path) -> str:
    """Littéral SQL texte (chemin : barres obliques)."""
    return "'" + str(value).replace("\\", "/").replace("'", "''") + "'"


def port_relation(run_dir: Path, node: str, port: str) -> Relation | None:
    """La requête qui lit le port `node/port` ; None : pas de données (port vide, cache purgé). Lève `SourceChanged`
    si un fichier lu a changé depuis le run, ou si un port désigné par une définition n'a plus ses données."""
    slot = port_path(run_dir, node, port)
    data = port_data(slot)
    if data is not None:
        return Relation(f"SELECT * FROM read_parquet({sql_text(data)})", get_crs(pq.read_schema(data)), [data])
    try:
        spec = json.loads(sql_path(slot).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    ctes: list[str] = []
    files: list[Path] = []
    for name, ports in spec["inputs"].items():
        parts = []
        for up, up_port in ports:
            found = port_relation(run_dir, up, up_port)
            if found is None:
                raise SourceChanged(f"données de {up}/{up_port} introuvables ; relancer le flux")
            parts.append(f"SELECT * FROM ({found.sql})")
            files += [f for f in found.files if f not in files]
        ctes.append(f"{name} AS ({' UNION ALL BY NAME '.join(parts)})")
    prefix = f"WITH {', '.join(ctes)} " if ctes else ""
    return Relation(f"{prefix}{spec['query']}", spec.get("crs"), files)


def write_query(
    con: duckdb.DuckDBPyConnection, query: str, target: Path, crs: str | None, block_rows: int = duck.DEFAULT_ROW_GROUP
) -> int:
    """Écrit le résultat de `query` dans `target` (port au format habituel, SCR compris), tout ou rien, par blocs de
    `block_rows` lignes (`duck.fit`) ; son nombre de lignes."""
    tmp = target.with_suffix(".parquet.tmp")
    target.parent.mkdir(parents=True, exist_ok=True)
    meta = f", KV_METADATA {{'etl.crs': {sql_text(crs)}}}" if crs else ""
    options = f"FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE {int(block_rows)}{meta}"
    try:
        con.execute(f"COPY ({query}) TO {sql_text(tmp)} ({options})")
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return pq.ParquetFile(target).metadata.num_rows


def connect_for(
    relation: Relation, kind: duck.Kind, directories: list[Path] | None = None
) -> tuple[duckdb.DuckDBPyConnection, int]:
    """Connexion DuckDB qui lit `relation` (extension spatiale si ses expressions en demandent), réglée sur la largeur
    de ses données, et qui n'atteint rien d'autre que ses fichiers et `directories` (`duck.confine`) ; et la taille des
    blocs qu'elle écrit (`duck.fit`)."""
    con = duck.connect(kind)
    if relation.needs_spatial():
        duck.load_spatial(con)
    block_rows = duck.fit(con, relation.files)
    duck.confine(con, directories or [], relation.files)
    return con, block_rows


def materialize(
    run_dir: Path, node: str, port: str, kind: duck.Kind = "inspect", abandoned: Callable[[], bool] | None = None
) -> Path | None:
    """Écrit le fichier d'un port défini par une requête (`<port>.parquet`, à côté de sa définition) ; son chemin.
    Sans effet pour un port qui a déjà un fichier ; None : pas de données. Dès que `abandoned()` est vrai, le calcul
    s'arrête (`duckdb.InterruptException`), sans rien écrire."""
    slot = port_path(run_dir, node, port)
    data = port_data(slot)
    if data is not None:
        return data
    relation = port_relation(run_dir, node, port)
    if relation is None:
        return None
    con, block_rows = connect_for(relation, kind, [slot.parent])
    try:
        with duck.interrupted_when(con, abandoned):
            write_query(con, relation.sql, slot, relation.crs, block_rows)
    finally:
        con.close()
    return slot


def _link(src: Path, dst: Path) -> None:
    """Lien physique, sinon copie (autre volume)."""
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def link_port(src: Path, dst: Path) -> bool:
    """Le port `dst` reprend les données du port `src` (son fichier, sa référence ou sa définition) ; faux si `src` n'en
    a pas."""
    for s, d in zip(_forms(src), _forms(dst), strict=True):
        if s.is_file():
            d.parent.mkdir(parents=True, exist_ok=True)
            for old in _forms(dst):
                old.unlink(missing_ok=True)
            _link(s, d)
            return True
    return False


def read_meta(node_dir: Path) -> dict | None:
    try:
        return json.loads((node_dir / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_meta(node_dir: Path, meta: dict) -> None:
    node_dir.mkdir(parents=True, exist_ok=True)
    (node_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def reuse_files(src_dir: Path, dst_dir: Path) -> None:
    """Rend les fichiers d'un nœud déjà calculé disponibles dans le nouveau run (liens physiques, sinon copies),
    sous-dossiers compris (rasters produits par le nœud)."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    for f in src_dir.iterdir():
        target = dst_dir / f.name
        if f.is_dir():
            reuse_files(f, target)
            continue
        if target.exists():
            target.unlink()
        _link(f, target)


def rebase_rasters(node_dir: Path, old_run: Path, new_run: Path) -> None:
    """Après `reuse_files` : les rasters d'un nœud repris désignent les fichiers du nouveau run, pas ceux de l'ancien,
    purgé ensuite. Les ports (colonne `raster.uri`) et les VRT (chemins de leurs sources) concernés sont réécrits dans
    de nouveaux fichiers : ceux du dossier sont des liens vers les fichiers de l'ancien run, qui restent intacts."""
    pairs = list(dict.fromkeys([(str(old_run), str(new_run)), (old_run.as_posix(), new_run.as_posix())]))

    def rebase(uri: str | None) -> str | None:
        for old, new in pairs:
            if uri and uri.startswith(old):
                return new + uri[len(old) :]
        return uri

    for path in node_dir.glob("*.parquet"):
        schema = pq.read_schema(path)
        if RASTER not in schema.names or not pa.types.is_struct(schema.field(RASTER).type):
            continue
        table = pq.read_table(path)
        index = schema.get_field_index(RASTER)
        col = table.column(index).combine_chunks()
        kind = col.type
        if kind.get_field_index("uri") < 0:
            continue
        children = [col.field(i) for i in range(kind.num_fields)]
        uris = children[kind.get_field_index("uri")].to_pylist()
        rebased = [rebase(u) for u in uris]
        if rebased == uris:
            continue
        children[kind.get_field_index("uri")] = pa.array(rebased, pa.string())
        col = pa.StructArray.from_arrays(children, fields=list(kind), mask=col.is_null())
        tmp = path.with_name(path.name + ".tmp")  # nouveau fichier : le lien vers l'ancien run reste intact
        pq.write_table(table.set_column(index, schema.field(index), col), tmp)
        os.replace(tmp, path)
    for path in (node_dir / "rasters").glob("*.vrt"):
        text = path.read_text(encoding="utf-8")
        new = text
        for old, repl in pairs:
            new = new.replace(xml_escape(old), xml_escape(repl))
        if new != text:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(new.encode("utf-8"))
            os.replace(tmp, path)


def batch_rows_for(files: list[Path], batch_rows: int) -> int:
    """Lignes par lot : `batch_rows`, moins pour des lignes larges (taille moyenne d'une ligne lue dans les
    métadonnées Parquet), pour qu'un lot tienne dans sa part du budget mémoire du run (`duck.batch_bytes`)."""
    rows = size = 0
    for f in files:
        meta = pq.ParquetFile(f).metadata
        rows += meta.num_rows
        size += sum(meta.row_group(i).total_byte_size for i in range(meta.num_row_groups))
    if not rows or not size:
        return batch_rows
    return min(batch_rows, max(1024, duck.batch_bytes() * rows // size))


def port_source(run_dir: Path, node: str, port: str) -> Path | Relation | None:
    """Le fichier d'un port, sinon la requête qui le lit (port défini à la demande) ; None : pas de données."""
    data = port_data(port_path(run_dir, node, port))
    return data if data is not None else port_relation(run_dir, node, port)


class PortSource:
    """Lots d'un port d'entrée : ses fichiers Parquet, ou les requêtes des ports définis à la demande (ADR 0044), lues
    par DuckDB, à la suite, lot par lot. Les lots ne portent que le SCR du port, comme ceux qu'il a reçus (un fichier
    désigné garde ses autres métadonnées : « geo »…). `total` : nombre d'entités, s'il est connu d'avance."""

    def __init__(
        self, sources: list[Path | Relation], batch_rows: int, cancel: threading.Event, total: int | None = None
    ) -> None:
        self.sources = sources
        self.batch_rows = batch_rows
        self.cancel = cancel
        self.total = total
        self.rows = 0

    def __iter__(self) -> Iterator[pa.Table]:
        for source in self.sources:
            for table in self._file(source) if isinstance(source, Path) else self._query(source):
                if self.cancel.is_set():
                    raise Cancelled
                self.rows += table.num_rows
                yield table

    def _file(self, path: Path) -> Iterator[pa.Table]:
        pf = pq.ParquetFile(path)
        crs = get_crs(pf.schema_arrow)
        for rb in pf.iter_batches(batch_size=batch_rows_for([path], self.batch_rows)):
            yield with_crs(pa.Table.from_batches([rb]).replace_schema_metadata(None), crs)

    def _query(self, relation: Relation) -> Iterator[pa.Table]:
        con, _ = connect_for(relation, "heavy")
        try:
            with con.sql(relation.sql).to_arrow_reader(batch_rows_for(relation.files, self.batch_rows)) as reader:
                for rb in reader:
                    yield with_crs(pa.Table.from_batches([rb]), relation.crs)
        finally:
            con.close()


_END = object()  # fin normale du port
_FAILED = object()  # l'amont a échoué ou a été annulé


class Tap:
    """File bornée d'un port vers un consommateur branché en direct.

    - côté producteur : `put` attend qu'il y ait de la place (lève `Cancelled` si le run est annulé) ; si le
      consommateur a fermé la file (fini sans tout lire, ou en erreur), les lots sont ignorés : jamais de blocage ;
    - côté consommateur : `PortStream` lit les lots jusqu'à la fin, ou lève `Cancelled` si l'amont a échoué.
    """

    def __init__(self, cancel: threading.Event, size: int = 4) -> None:
        self._queue: queue.Queue = queue.Queue(size)
        self.cancel = cancel
        self.closed = False

    def put(self, item: object, *, final: bool = False) -> None:
        while not self.closed:
            if self.cancel.is_set():
                if final:
                    return
                raise Cancelled
            try:
                self._queue.put(item, timeout=0.1)
                return
            except queue.Full:
                continue

    def finish(self, ok: bool) -> None:
        self.put(_END if ok else _FAILED, final=True)

    def get(self) -> object:
        while True:
            try:
                return self._queue.get(timeout=0.1)
            except queue.Empty:
                if self.cancel.is_set():
                    raise Cancelled from None

    def close(self) -> None:
        """Le consommateur a fini : le producteur n'attend plus de place."""
        self.closed = True
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return


class PortStream:
    """Lots d'un port d'entrée branché en direct sur son amont (même interface que `PortSource`, lecture unique)."""

    def __init__(self, tap: Tap) -> None:
        self.tap = tap
        self.rows = 0
        self._read = False

    def __iter__(self) -> Iterator[pa.Table]:
        if self._read:
            raise ConfigError("une entrée branchée en direct ne peut être lue qu'une fois")
        self._read = True
        while True:
            item = self.tap.get()
            if item is _END:
                return
            if item is _FAILED:
                raise Cancelled
            self.rows += item.num_rows  # type: ignore[attr-defined]
            yield item  # type: ignore[misc]


def row_groups(table: pa.Table) -> Iterator[pa.Table]:
    """`table` en tranches d'au plus `ROW_GROUP_BYTES` (une ligne plus grosse reste seule) : un port aux géométries
    énormes (24 groupes fusionnés, 600 Mo) n'est pas écrit en un seul bloc, impossible à relire en peu de mémoire."""
    if table.nbytes <= ROW_GROUP_BYTES or table.num_rows < 2:
        yield table
        return
    sizes = np.full(table.num_rows, 0.0)
    geom_bytes = 0
    if "geometry" in table.column_names:
        lengths = pc.binary_length(table.column("geometry")).fill_null(0).to_numpy(zero_copy_only=False)
        sizes += lengths
        geom_bytes = int(lengths.sum())
    sizes += max(0, table.nbytes - geom_bytes) / table.num_rows
    group = (np.cumsum(sizes) - sizes) // ROW_GROUP_BYTES  # tranche de chaque ligne (la première ligne ouvre la sienne)
    cuts = np.flatnonzero(np.diff(group)) + 1
    start = 0
    for stop in [*cuts.tolist(), table.num_rows]:
        yield table.slice(start, stop - start)
        start = stop


def dictionary_columns(schema: pa.Schema) -> list[str] | bool:
    """Colonnes encodées par dictionnaire dans un fichier Parquet écrit par le moteur : le texte seulement (codes,
    catégories : fichier plus petit, filtres plus rapides). Sur la géométrie et les nombres, presque toujours tous
    différents, le dictionnaire doublait le temps d'écriture pour rien (ADR 0042)."""
    text = [f.name for f in schema if pa.types.is_string(f.type) or pa.types.is_large_string(f.type)]
    return text or False


class _Encoder:
    """Encode les lots d'un fichier Parquet dans un thread à part : le nœud prépare le lot suivant pendant ce temps
    (l'encodage libère le GIL). File bornée : au plus `size` lots en attente, le nœud attend au-delà."""

    def __init__(self, path: Path, schema: pa.Schema, size: int = 2) -> None:
        self._writer = pq.ParquetWriter(
            path, schema, compression=PORT_COMPRESSION, use_dictionary=dictionary_columns(schema)
        )
        self._queue: queue.Queue = queue.Queue(size)
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._loop, name="etl-port", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while (table := self._queue.get()) is not None:
            if self._error is None:
                try:
                    for part in row_groups(table):
                        self._writer.write_table(part)
                except BaseException as exc:  # noqa: BLE001 - rendue au nœud (disque plein…)
                    self._error = exc

    def write(self, table: pa.Table) -> None:
        if self._error is not None:
            raise self._error
        self._queue.put(table)

    def finish(self) -> None:
        """Attend la fin de l'encodage ; lève l'erreur d'écriture s'il y en a eu une."""
        if self._thread.is_alive():
            self._queue.put(None)
            self._thread.join()
        if self._error is not None:
            raise self._error

    def close(self) -> None:
        try:
            self.finish()
        finally:
            self._writer.close()


class PortSink:
    """Écrit les lots d'un port de sortie ; le fichier n'apparaît qu'à la fermeture (renommage atomique).

    `taps` : files des consommateurs branchés en direct. `store=False` : pas de fichier (run sans cache dont tous les
    consommateurs sont branchés en direct), seulement les comptes et les files.
    """

    def __init__(
        self,
        path: Path,
        declared: pa.Schema | None,
        on_rows: Callable[[int], None],
        taps: list[Tap] | None = None,
        store: bool = True,
    ) -> None:
        self.path = path
        self.declared = declared
        self.on_rows = on_rows
        self.taps = taps or []
        self.store = store
        self.rows = 0
        self._writer: _Encoder | None = None
        self._schema: pa.Schema | None = None
        self._tmp = path.with_suffix(".parquet.tmp")

    def write(self, table: pa.Table) -> None:
        if table.num_rows == 0:
            return
        if self._schema is None:
            self._schema = table.schema
            if self.store:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._writer = _Encoder(self._tmp, self._schema)
        else:
            try:
                table = conform(table, self._schema)
            except ValueError as exc:
                raise ConfigError(f"port « {self.path.stem} » : schéma incohérent entre deux lots ({exc})") from exc
        table = table.replace_schema_metadata(self._schema.metadata)
        if self._writer is not None:
            self._writer.write(table)
        for tap in self.taps:
            tap.put(table)
        self.rows += table.num_rows
        self.on_rows(self.rows)

    def finish(self) -> None:
        """Le nœud a tout émis : attend la fin de l'écriture du fichier (une erreur d'écriture est celle du nœud)."""
        if self._writer is not None:
            self._writer.finish()

    def close(self, ok: bool = True) -> None:
        try:
            self._close_file(ok)
        finally:
            for tap in self.taps:
                tap.finish(ok)

    def _close_file(self, ok: bool) -> None:
        if self._writer is not None:
            writer, self._writer = self._writer, None
            try:
                writer.close()
            except BaseException:
                if ok:  # erreur d'écriture non rendue par `finish` : le fichier n'est pas publié
                    self._tmp.unlink(missing_ok=True)
                    raise
            if ok:
                os.replace(self._tmp, self.path)
            else:
                self._tmp.unlink(missing_ok=True)
        elif ok and self.declared is not None and self.store and self._schema is None:
            # aucune entité : on écrit quand même un fichier vide pour que l'inspecteur voie les colonnes
            self.path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(self.declared.empty_table(), self._tmp)
            os.replace(self._tmp, self.path)


class Throttle:
    """Limite la fréquence des événements de comptage (sinon un événement par lot)."""

    def __init__(self, interval: float = 0.25) -> None:
        self.interval = interval
        self._last = 0.0

    def ready(self) -> bool:
        now = time.monotonic()
        if now - self._last >= self.interval:
            self._last = now
            return True
        return False
