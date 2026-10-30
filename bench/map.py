"""Banc de la carte de l'inspecteur : préparation de la vue (étape par étape), tuiles à froid, sélection.

python bench/map.py                              # parcelles, points, polygone fusionné (générés si besoin)
python bench/map.py D:/donnees/dissolve.parquet  # un GeoParquet de son choix (lu, jamais modifié)
python bench/map.py --ref HEAD~1 fichier.parquet # préparation seule, avec la vue d'une autre version (avant / après)

Chaque fichier est lié (ou copié) dans un cache temporaire : la vue et les tuiles y sont écrites, puis effacées.
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from etl_api import data_service as ds
from etl_engine import view as views

sys.path.insert(0, str(Path(__file__).parent))
from generate import generate, generate_fused, generate_parcels  # noqa: E402

DATA = Path(__file__).parent / "data"
ROOT = Path(__file__).parent.parent
ZOOMS = (5, 8, 10, 12, 14)


def default_files() -> list[Path]:
    found = []
    for name, make in (
        ("parcelles_1000000.parquet", lambda p: generate_parcels(1_000_000, p)),
        ("points_1000000.parquet", lambda p: generate(1_000_000, p)),
        ("fused_300000.parquet", lambda p: generate_fused(300_000, p)),
    ):
        path = DATA / name
        if not path.exists():
            print(f"génération de {name}…", flush=True)
            make(path)
        found.append(path)
    return found


def view_module(ref: str):
    """`etl_engine.view` tel qu'il était à la révision `ref` (pour mesurer avant / après)."""
    source = subprocess.run(
        ["git", "show", f"{ref}:engine/src/etl_engine/view.py"], cwd=ROOT, capture_output=True, check=True
    ).stdout
    path = Path(tempfile.mkdtemp()) / "view_ref.py"
    path.write_bytes(source)
    spec = importlib.util.spec_from_file_location("view_ref", path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def place(src: Path, root: Path) -> Path:
    raw = root / "run" / "node" / "output.parquet"
    raw.parent.mkdir(parents=True)
    try:
        os.link(src, raw)
    except OSError:
        shutil.copyfile(src, raw)
    return raw


def tiles(raw: Path, root: Path, extent: list[float]) -> dict[int, list[float]]:
    """Tuiles à froid (calcul complet, cache disque vide) : 3 × 3 tuiles autour du centre de l'emprise, par zoom."""
    port = ds.open_port(ds.Duck(root), "run", "node", "output", prepare=False)
    port.view, port.entities, port.meta = views.view_path(raw), views.entities_path(raw), views.read_view_meta(raw)
    cx, cy = (extent[0] + extent[2]) / 2, (extent[1] + extent[3]) / 2
    found: dict[int, list[float]] = {}
    for z in ZOOMS:
        size = views.tile_size(z)
        x, y = int((cx + views.WORLD) // size), int((views.WORLD - cy) // size)
        found[z] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                t0 = time.perf_counter()
                ds.tile(port, z, x + dx, y + dy, ds.Query())
                found[z].append(time.perf_counter() - t0)
    return found


def selection(raw: Path, root: Path) -> float:
    port = ds.open_port(ds.Duck(root), "run", "node", "output", prepare=False)
    port.view, port.entities, port.meta = views.view_path(raw), views.entities_path(raw), views.read_view_meta(raw)
    t0 = time.perf_counter()
    ds.feature(port, 0)
    return time.perf_counter() - t0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("--ref", help="révision git dont la vue est mesurée (préparation seule)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="  %(message)s")
    module = view_module(args.ref) if args.ref else views
    for src in args.files or default_files():
        root = Path(tempfile.mkdtemp(prefix="etl-bench-map-"))
        try:
            raw = place(src, root)
            print(f"\n{src.name} ({src.stat().st_size / 2**20:.0f} Mo)", flush=True)
            t0 = time.perf_counter()
            meta = module.build_view(raw)
            print(f"  préparation : {time.perf_counter() - t0:.1f} s ({meta['rows']} entités)", flush=True)
            if args.ref:
                continue
            for z, times in tiles(raw, root, meta["extent_3857"]).items():
                print(f"  tuiles z{z} : médiane {statistics.median(times):.2f} s, max {max(times):.2f} s", flush=True)
            print(f"  sélection de l'entité 0 : {selection(raw, root):.2f} s")
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
