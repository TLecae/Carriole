"""Stockage des ports : un GeoParquet par port de sortie de chaque nœud (voir ADR 0003 et 0010).

Disposition : `<run>/<nœud>/<port>.parquet` et `<run>/<nœud>/meta.json` (empreinte, comptes, statut).

Un consommateur « branché en direct » (voir ADR 0013) reçoit aussi les lots par une file bornée (`Tap`), au fur et
à mesure qu'ils sont écrits : il travaille en même temps que son amont au lieu d'attendre puis de relire le fichier.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from etl_engine.data import conform, get_crs, with_crs
from etl_engine.sdk import Cancelled, ConfigError

PORT_COMPRESSION = os.environ.get("ETL_PORT_COMPRESSION", "zstd")
ROW_GROUP_BYTES = 128 * 2**20  # un bloc Parquet ne dépasse pas ça (sauf une seule ligne plus grosse) : relu par blocs


def port_path(run_dir: Path, node_id: str, port: str) -> Path:
    return run_dir / node_id / f"{port}.parquet"


def read_meta(node_dir: Path) -> dict | None:
    try:
        return json.loads((node_dir / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_meta(node_dir: Path, meta: dict) -> None:
    node_dir.mkdir(parents=True, exist_ok=True)
    (node_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def reuse_files(src_dir: Path, dst_dir: Path) -> None:
    """Rend les fichiers d'un nœud déjà calculé disponibles dans le nouveau run (lien physique, sinon copie)."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    for f in src_dir.iterdir():
        target = dst_dir / f.name
        if target.exists():
            target.unlink()
        try:
            os.link(f, target)
        except OSError:
            shutil.copy2(f, target)


class PortSource:
    """Lots d'un port d'entrée : un ou plusieurs fichiers Parquet lus à la suite, lot par lot."""

    def __init__(self, files: list[Path], batch_rows: int, cancel: threading.Event) -> None:
        self.files = files
        self.batch_rows = batch_rows
        self.cancel = cancel
        self.rows = 0

    def __iter__(self) -> Iterator[pa.Table]:
        for path in self.files:
            pf = pq.ParquetFile(path)
            crs = get_crs(pf.schema_arrow)
            for rb in pf.iter_batches(batch_size=self.batch_rows):
                if self.cancel.is_set():
                    raise Cancelled
                self.rows += rb.num_rows
                yield with_crs(pa.Table.from_batches([rb]), crs)


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
        self._writer: pq.ParquetWriter | None = None
        self._schema: pa.Schema | None = None
        self._tmp = path.with_suffix(".parquet.tmp")

    def write(self, table: pa.Table) -> None:
        if table.num_rows == 0:
            return
        if self._schema is None:
            self._schema = table.schema
            if self.store:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._writer = pq.ParquetWriter(self._tmp, self._schema, compression=PORT_COMPRESSION)
        else:
            try:
                table = conform(table, self._schema)
            except ValueError as exc:
                raise ConfigError(f"port « {self.path.stem} » : schéma incohérent entre deux lots ({exc})") from exc
        table = table.replace_schema_metadata(self._schema.metadata)
        if self._writer is not None:
            for part in row_groups(table):
                self._writer.write_table(part)
        for tap in self.taps:
            tap.put(table)
        self.rows += table.num_rows
        self.on_rows(self.rows)

    def close(self, ok: bool = True) -> None:
        try:
            self._close_file(ok)
        finally:
            for tap in self.taps:
                tap.finish(ok)

    def _close_file(self, ok: bool) -> None:
        if self._writer is not None:
            self._writer.close()
            self._writer = None
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
