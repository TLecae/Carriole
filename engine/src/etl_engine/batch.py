"""Traitement par lots : lancer un même flux plusieurs fois avec des paramètres différents, en parallèle ou non.

Chaque exécution est un sous-processus `etl run` : mémoire isolée, plantage sans conséquence pour les autres, et le
même moteur que la ligne de commande. Sert à `etl run --each …` et au modificateur `RunFlow`.
"""

from __future__ import annotations

import csv
import glob
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from etl_engine import duck

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


@dataclass
class BatchItem:
    """Une exécution du lot : ses paramètres publiés et un libellé lisible (nom du fichier, ligne du CSV…)."""

    params: dict[str, str]
    label: str = ""


@dataclass
class BatchResult:
    item: BatchItem
    returncode: int
    seconds: float
    error: str = ""  # dernières lignes d'erreur du sous-processus
    output: str = field(default="", repr=False)

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def expand_each(pattern: str) -> list[str]:
    """Fichiers d'un motif (`D:\\dossier\\*.shp`, `**` accepté), triés."""
    found = sorted(glob.glob(pattern, recursive=True))
    return [os.path.normpath(f) for f in found if os.path.isfile(f)]


def items_from_values(name: str, values: Sequence[str]) -> list[BatchItem]:
    return [BatchItem({name: v}, os.path.basename(v) or v) for v in values]


def items_from_list(name: str, list_file: str | Path) -> list[BatchItem]:
    """Un fichier texte, une valeur par ligne (lignes vides et `#commentaires` ignorés)."""
    lines = Path(list_file).read_text(encoding="utf-8-sig").splitlines()
    return items_from_values(name, [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")])


def items_from_csv(csv_file: str | Path) -> list[BatchItem]:
    """Un CSV : la première ligne donne les noms des paramètres, chaque ligne suivante est une exécution."""
    text = Path(csv_file).read_text(encoding="utf-8-sig")
    sample = text[:4096]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    rows = list(csv.DictReader(text.splitlines(), delimiter=delimiter))
    out = []
    for i, row in enumerate(rows, 1):
        params = {k.strip(): (v or "").strip() for k, v in row.items() if k}
        first = next(iter(params.values()), "")
        out.append(BatchItem(params, os.path.basename(first) or f"ligne {i}"))
    return out


def command_for(
    flow_path: str, params: dict[str, str], extra: Sequence[str] = (), python: str | None = None
) -> list[str]:
    cmd = [python or sys.executable, "-m", "etl_engine.cli", "run", str(flow_path)]
    for key, value in params.items():
        cmd += ["-p", f"{key}={value}"]
    return [*cmd, *extra]


def run_one(
    flow_path: str,
    item: BatchItem,
    extra: Sequence[str] = (),
    cancel: threading.Event | None = None,
    python: str | None = None,
    share: int = 1,
) -> BatchResult:
    """Lance un flux dans son processus ; `share` : nombre de flux lancés ensemble (chacun a sa part de la machine)."""
    started = time.perf_counter()
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    if share > 1:
        env["ETL_RUN_MEMORY_MB"] = str(max(512, duck.machine_budget_mb() // share))
        env["ETL_RUN_THREADS"] = str(max(2, duck.machine_threads() // share))
    proc = subprocess.Popen(  # noqa: S603 - commande construite ici, sans interpréteur de commandes
        command_for(flow_path, item.params, extra, python),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        creationflags=CREATE_NO_WINDOW,
    )
    out_chunks: list[bytes] = []
    reader = threading.Thread(target=lambda: out_chunks.append(proc.stdout.read()), daemon=True)  # type: ignore[union-attr]
    reader.start()
    while proc.poll() is None:
        if cancel is not None and cancel.is_set():
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
            break
        time.sleep(0.2)
    reader.join(timeout=5)
    output = b"".join(out_chunks).decode("utf-8", errors="replace")
    code = proc.returncode if proc.returncode is not None else 1
    error = ""
    if code != 0:
        lines = [ln for ln in output.splitlines() if ln.strip()]
        error = " | ".join(lines[-3:]) if lines else f"code de sortie {code}"
    return BatchResult(item, code, round(time.perf_counter() - started, 3), error, output)


def run_batch(
    flow_path: str,
    items: Sequence[BatchItem],
    *,
    parallel: int = 1,
    extra: Sequence[str] = (),
    stop_on_error: bool = False,
    cancel: threading.Event | None = None,
    on_done: Callable[[int, BatchResult], None] | None = None,
    python: str | None = None,
) -> list[BatchResult]:
    """Exécute le flux pour chaque élément ; renvoie les résultats dans l'ordre des éléments."""
    cancel = cancel or threading.Event()
    results: list[BatchResult | None] = [None] * len(items)
    lock = threading.Lock()

    def work(index: int) -> None:
        if cancel.is_set():
            return
        result = run_one(flow_path, items[index], extra, cancel, python, share=max(1, min(parallel, len(items))))
        with lock:
            results[index] = result
            if on_done:
                on_done(index, result)
        if stop_on_error and not result.ok:
            cancel.set()

    with ThreadPoolExecutor(max_workers=max(1, parallel), thread_name_prefix="etl-batch-run") as pool:
        list(pool.map(work, range(len(items))))
    return [r for r in results if r is not None]
