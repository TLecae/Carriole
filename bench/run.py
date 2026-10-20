"""Benchmark du moteur : temps et mémoire crête de flux types sur des jeux synthétiques.

python bench/run.py 1000000                     # génère les jeux si besoin, lance tous les cas
python bench/run.py 1000000 chaine,clipper      # quelques cas seulement
python bench/run.py 1000000 --tag avant         # écrit bench/results_N_avant.md
"""

from __future__ import annotations

import multiprocessing
import os
import platform
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import psutil
from etl_engine.executor import run_flow
from etl_engine.flow import Flow
from etl_engine.registry import Registry

sys.path.insert(0, str(Path(__file__).parent))
from generate import generate, generate_parcels, generate_raster, generate_zones  # noqa: E402

DATA = Path(__file__).parent / "data"

# Objectifs proposés (docs/plan_jalons.md) : (secondes, Mo de mémoire crête) pour 1 M et 10 M d'entités.
TARGETS = {
    1_000_000: {
        "filtre": (10, 1500),
        "calcul": (8, 1500),
        "reprojection": (20, 1500),
        "gpkg": (60, 1500),
        "csv": (30, 1500),
    },
    10_000_000: {
        "filtre": (90, 3000),
        "calcul": (70, 3000),
        "reprojection": (200, 3000),
        "gpkg": (600, 3000),
        "csv": (300, 3000),
    },
}


def datasets(n: int) -> dict[str, Path]:
    """Les jeux du banc pour `n` points (générés au premier usage)."""
    files = {
        "points": (DATA / f"points_{n}.parquet", lambda p: generate(n, p)),
        "parcelles": (DATA / f"parcelles_{n // 10}.parquet", lambda p: generate_parcels(n // 10, p)),
        "zones": (DATA / f"zones_{n // 100}.parquet", lambda p: generate_zones(n // 100, p)),
        "mnt": (DATA / "mnt.tif", generate_raster),
    }
    for path, make in files.values():
        if not path.exists():
            t0 = time.perf_counter()
            make(path)
            print(f"jeu généré en {time.perf_counter() - t0:.1f} s : {path}")
    return {name: path for name, (path, _) in files.items()}


def _flow(nodes: list[dict], links: list[tuple[str, str, str, str]]) -> Flow:
    return Flow.model_validate(
        {
            "nodes": nodes,
            "links": [{"id": f"l{i}", "from": [a, ap], "to": [b, bp]} for i, (a, ap, b, bp) in enumerate(links)],
        }
    )


def _read(node_id: str, path: Path) -> dict:
    return {"id": node_id, "type": "Reader", "params": {"path": str(path)}}


def _write(out: Path, name: str) -> dict:
    return {"id": "write", "type": "Writer", "params": {"path": str(out / f"{name}.parquet")}}


def simple(case: str, d: dict[str, Path], out: Path) -> Flow:
    """Les cas du jalon 7 : Reader → [modificateur] → Writer sur les points."""
    reader = _read("read", d["points"])
    if case == "filtre":
        mid = [
            {"id": "f", "type": "ExtractByExpression", "params": {"conditions": ['"dep" = \'31\' AND "pop" > 10000']}}
        ]
        links, tail = [("read", "output", "f", "input")], ("f", "Passed")
        sink = {"path": str(out / "filtre.parquet")}
    elif case == "calcul":
        mid = [
            {
                "id": "c",
                "type": "AddAttributes",
                "params": {"attributes": [{"name": "ratio", "expression": '"val" / ("pop" + 1)'}]},
            },
            {"id": "r", "type": "RenameAttributes", "params": {"renamings": [{"source": "val", "target": "valeur"}]}},
        ]
        links, tail = [("read", "output", "c", "input"), ("c", "output", "r", "input")], ("r", "output")
        sink = {"path": str(out / "calcul.parquet")}
    elif case == "tri":
        mid = [{"id": "s", "type": "OrderBy", "params": {"keys": [{"attribute": "dep"}, {"attribute": "pop"}]}}]
        links, tail = [("read", "output", "s", "input")], ("s", "output")
        sink = {"path": str(out / "tri.parquet")}
    elif case == "statistiques":
        mid = [
            {
                "id": "s",
                "type": "StatisticsByGroup",
                "params": {"group_by": ["dep"], "attributes": ["pop", "val"], "functions": ["sum", "mean", "max"]},
            }
        ]
        links, tail = [("read", "output", "s", "input")], ("s", "output")
        sink = {"path": str(out / "stats.parquet")}
    elif case == "reprojection":
        mid = [{"id": "p", "type": "Reproject", "params": {"target": "EPSG:4326"}}]
        links, tail = [("read", "output", "p", "input")], ("p", "output")
        sink = {"path": str(out / "reproj.parquet")}
    elif case == "gpkg":
        mid, links, tail = [], [], ("read", "output")
        sink = {"path": str(out / "sortie.gpkg"), "layer": "points"}
    else:  # csv
        mid, links, tail = [], [], ("read", "output")
        sink = {"path": str(out / "sortie.csv")}
    nodes = [reader, *mid, {"id": "write", "type": "Writer", "params": sink}]
    return _flow(nodes, [*links, (*tail, "write", "input")])


def chaine(d: dict[str, Path], out: Path) -> Flow:
    """Cinq modificateurs au fil de l'eau à la suite : mesure le passage d'un nœud au suivant."""
    nodes = [
        _read("read", d["points"]),
        {
            "id": "c",
            "type": "AddAttributes",
            "params": {"attributes": [{"name": "ratio", "expression": '"val" / ("pop" + 1)'}]},
        },
        {"id": "t", "type": "ExtractByExpression", "params": {"conditions": ['"pop" > 1000']}},
        {"id": "p", "type": "Reproject", "params": {"target": "EPSG:4326"}},
        {"id": "b", "type": "AddAttributes", "params": {"attributes": [{"name": "x2", "expression": '"val" * 2'}]}},
        {"id": "r", "type": "RenameAttributes", "params": {"renamings": [{"source": "val", "target": "valeur"}]}},
        _write(out, "chaine"),
    ]
    links = [
        ("read", "output", "c", "input"),
        ("c", "output", "t", "input"),
        ("t", "Passed", "p", "input"),
        ("p", "output", "b", "input"),
        ("b", "output", "r", "input"),
        ("r", "output", "write", "input"),
    ]
    return _flow(nodes, links)


def two_inputs(kind: str, params: dict, left: tuple[str, Path], right: tuple[str, Path], port: str, out: Path) -> Flow:
    """Modificateur à deux entrées : `left` et `right` = (port d'entrée, fichier)."""
    nodes = [
        _read("a", left[1]),
        _read("b", right[1]),
        {"id": "x", "type": kind, "params": params},
        _write(out, kind),
    ]
    links = [("a", "output", "x", left[0]), ("b", "output", "x", right[0]), ("x", port, "write", "input")]
    return _flow(nodes, links)


def one_input(kind: str, params: dict, src: Path, port: str, out: Path) -> Flow:
    nodes = [_read("a", src), {"id": "x", "type": kind, "params": params}, _write(out, kind)]
    return _flow(nodes, [("a", "output", "x", "input"), ("x", port, "write", "input")])


def zonal(d: dict[str, Path], out: Path) -> Flow:
    nodes = [
        _read("a", d["zones"]),
        {"id": "r", "type": "RasterReader", "params": {"path": str(d["mnt"])}},
        {"id": "x", "type": "ZonalStatistics", "params": {"stats": ["mean", "max"]}},
        _write(out, "zonal"),
    ]
    links = [("a", "output", "x", "zones"), ("r", "output", "x", "raster"), ("x", "output", "write", "input")]
    return _flow(nodes, links)


CASES: dict[str, Callable[[dict[str, Path], Path], Flow]] = {
    **{
        name: (lambda d, out, name=name: simple(name, d, out))
        for name in ("filtre", "calcul", "reprojection", "tri", "statistiques", "gpkg", "csv")
    },
    "chaine": chaine,
    "tampon": lambda d, out: one_input("Buffer", {"distance": 10}, d["parcelles"], "output", out),
    "clipper": lambda d, out: two_inputs(
        "Clip", {"mode": "union"}, ("clippee", d["parcelles"]), ("clipper", d["zones"]), "Inside", out
    ),
    "point_sur_surface": lambda d, out: two_inputs(
        "PointsInPolygons",
        {"attributes": ["commune"]},
        ("points", d["points"]),
        ("areas", d["parcelles"]),
        "Inside",
        out,
    ),
    "relateur": lambda d, out: two_inputs(
        "JoinByLocation", {}, ("base", d["parcelles"]), ("related", d["points"]), "output", out
    ),
    "filtre_spatial": lambda d, out: two_inputs(
        "ExtractByLocation", {}, ("candidate", d["points"]), ("filter", d["zones"]), "Passed", out
    ),
    "voisins": lambda d, out: two_inputs(
        "JoinByNearest", {"count": 3}, ("base", d["zones"]), ("candidate", d["points"]), "Matched", out
    ),
    "dissolver": lambda d, out: one_input("Dissolve", {}, d["parcelles"], "Dissolved", out),
    "dissolver_groupes": lambda d, out: one_input(
        "Dissolve", {"group_by": ["commune"]}, d["parcelles"], "Dissolved", out
    ),
    "superposition": lambda d, out: one_input("OverlayPolygons", {}, d["zones"], "Area", out),
    "zonal": zonal,
}


def run_case(case: str, d: dict[str, Path], registry: Registry | None = None) -> tuple[int, float, float]:
    """(entités écrites, secondes, pic mémoire en Mo) d'un cas."""
    registry = registry or Registry.discover()
    work = Path(tempfile.mkdtemp(prefix="etl-bench-"))
    try:
        t0 = time.perf_counter()
        res = run_flow(CASES[case](d, work), registry, work / "run", cache=False, timeout=1800)
        elapsed = time.perf_counter() - t0
        if res.status != "done":
            raise SystemExit(f"{case} : {res.status} {res.error}")
        peak = max(r.rss_mb or 0 for r in res.nodes.values())
        return res.nodes["write"].rows_in.get("input", 0), elapsed, peak
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _child(case: str, d: dict[str, Path], queue: multiprocessing.Queue) -> None:
    queue.put(run_case(case, d))


def two_runs(d: dict[str, Path]) -> tuple[int, float, float]:
    """Deux runs « chaine » en même temps, chacun dans son processus (comme deux workers)."""
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    t0 = time.perf_counter()
    procs = [ctx.Process(target=_child, args=("chaine", d, queue)) for _ in range(2)]
    for p in procs:
        p.start()
    results = [queue.get() for _ in procs]
    for p in procs:
        p.join()
    elapsed = time.perf_counter() - t0
    return sum(r[0] for r in results), elapsed, sum(r[2] for r in results)


def _num(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def main() -> None:
    # Budget mémoire par défaut d'un worker : DuckDB déborde sur disque au-delà.
    os.environ.setdefault("ETL_MEMORY_BUDGET_MB", "3072")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    tag = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--tag" and i + 1 < len(sys.argv)), "")
    args = [a for a in args if a != tag]
    n = int(args[0]) if args else 1_000_000
    cases = args[1].split(",") if len(args) > 1 else [*CASES, "deux_runs"]
    d = datasets(n)
    registry = Registry.discover()
    rows = []
    for case in cases:
        out_rows, elapsed, peak = two_runs(d) if case == "deux_runs" else run_case(case, d, registry)
        rows.append((case, out_rows, elapsed, peak))
        print(f"{case:18} {out_rows:>10} entités  {elapsed:7.1f} s  pic {peak:7.0f} Mo", flush=True)

    lines = [
        f"# Benchmark ({_num(n)} points, {_num(n // 10)} parcelles, {_num(n // 100)} zones)",
        "",
        f"Machine : {platform.processor() or platform.machine()}, {psutil.cpu_count()} cœurs logiques, "
        f"{psutil.virtual_memory().total / 2**30:.0f} Go, Python {platform.python_version()}, {platform.system()}.",
        "",
        "| Cas | Entités écrites | Temps (s) | Pic mémoire (Mo) | Objectif (s / Mo) |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for case, out_rows, elapsed, peak in rows:
        goal = TARGETS.get(n, {}).get(case)
        target = f"{goal[0]} / {goal[1]}" if goal else "-"
        lines.append(f"| {case} | {out_rows} | {elapsed:.1f} | {peak:.0f} | {target} |")
    name = f"results_{n}{'_' + tag if tag else ''}.md"
    (Path(__file__).parent / name).write_bytes(("\n".join(lines) + "\n").encode())


if __name__ == "__main__":
    main()
