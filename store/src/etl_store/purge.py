"""Rétention et purge du cache : on supprime les données des runs terminés, jamais celles qui servent encore.

Deux politiques : `purge_cache` (serveur : ancienneté et taille) et `release_cache` (poste de bureau : seulement tant
que le flux est ouvert).
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from etl_store.store import Store

log = logging.getLogger("etl.purge")
DAY = 86400.0
ORPHAN_AGE = 600.0  # un dossier sans run connu, plus vieux que ça, est un reste (run supprimé, plantage)
EXPORTS = "_exports"  # fichiers temporaires des exports de l'inspecteur
STAGING = "_staging"  # écritures en cours vers un partage réseau (etl_engine.staging)


@dataclass
class PurgeReport:
    deleted: list[str] = field(default_factory=list)  # runs dont le cache a été supprimé
    reasons: dict[str, str] = field(default_factory=dict)  # run -> "ancienneté" | "taille"
    orphans: list[str] = field(default_factory=list)  # dossiers sans run connu, supprimés
    freed_bytes: int = 0
    remaining_bytes: int = 0


def dir_size(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            with contextlib.suppress(OSError):  # fichier supprimé entre-temps
                total += os.path.getsize(os.path.join(root, name))
    return total


def newest_mtime(path: Path) -> float:
    """Dernière modification d'un dossier ou de l'un de ses fichiers (celle du dossier ne bouge pas à l'écriture)."""
    newest = path.stat().st_mtime
    for root, _, files in os.walk(path):
        for name in files:
            with contextlib.suppress(OSError):
                newest = max(newest, os.path.getmtime(os.path.join(root, name)))
    return newest


def release_cache(store: Store, cache_dir: Path, held: set[str], now: float | None = None) -> PurgeReport:
    """Poste de bureau : ne garde que le cache des runs en cours ou en attente (et de ceux qu'ils reprennent) et des
    runs `held`, affichés dans une page ouverte. Flux fermé, fenêtre fermée, application ou moteur plantés : leurs
    données partent au balayage suivant. Un run n'est noté purgé que si son dossier a disparu (fichier encore ouvert
    sous Windows : réessayé au balayage suivant)."""
    now = now or time.time()
    report = PurgeReport()
    if not cache_dir.is_dir():
        return report
    keep = store.protected_runs() | held
    for r in store.runs_for_purge():
        if r["id"] in keep:
            continue
        folder = cache_dir / r["id"]
        if folder.exists():
            size = dir_size(folder)
            shutil.rmtree(folder, ignore_errors=True)
            if folder.exists():
                continue
            report.freed_bytes += size
        report.deleted.append(r["id"])
        report.reasons[r["id"]] = "flux fermé"
    known = store.known_run_ids()
    for p in cache_dir.iterdir():
        if not p.is_dir() or p.name in (EXPORTS, STAGING) or p.name in known or p.name in keep:
            continue
        if now - p.stat().st_mtime > ORPHAN_AGE:
            report.freed_bytes += dir_size(p)
            shutil.rmtree(p, ignore_errors=True)
            report.orphans.append(p.name)
    store.mark_purged(report.deleted)
    if report.freed_bytes:
        freed = len(report.deleted) + len(report.orphans)
        log.info("cache libéré : %d run(s), %d Mo", freed, report.freed_bytes // 2**20)
    return report


def purge_cache(
    store: Store,
    cache_dir: Path,
    retention_days: float,
    max_bytes: int,
    now: float | None = None,
    dry_run: bool = False,
) -> PurgeReport:
    """Supprime d'abord les runs plus vieux que `retention_days`, puis les plus anciens jusqu'à passer sous `max_bytes`.

    Sont toujours épargnés : les runs en attente ou en cours, et ceux qu'ils reprennent. Un run purgé reste
    consultable (statut, journal, comptes) ; seules ses données disparaissent.
    """
    now = now or time.time()
    report = PurgeReport()
    if not cache_dir.is_dir():
        return report

    protected = store.protected_runs()
    known = store.known_run_ids()
    candidates = [r for r in store.runs_for_purge() if r["id"] not in protected]
    sizes = {r["id"]: dir_size(cache_dir / r["id"]) for r in candidates}
    total = sum(dir_size(p) for p in cache_dir.iterdir() if p.is_dir())

    def drop(run_id: str, reason: str) -> None:
        nonlocal total
        report.deleted.append(run_id)
        report.reasons[run_id] = reason
        report.freed_bytes += sizes[run_id]
        total -= sizes[run_id]
        if not dry_run:
            shutil.rmtree(cache_dir / run_id, ignore_errors=True)

    expired = {r["id"] for r in candidates if now - r["at"] > retention_days * DAY}
    for r in candidates:
        if r["id"] in expired:
            drop(r["id"], "ancienneté")
    for r in candidates:  # du plus ancien au plus récent, jusqu'à repasser sous la limite
        if total <= max_bytes:
            break
        if r["id"] not in expired:
            drop(r["id"], "taille")

    # Dossiers qu'aucun run ne réclame (run supprimé, écriture interrompue) : seulement s'ils sont anciens
    for p in cache_dir.iterdir():
        if not p.is_dir() or now - p.stat().st_mtime < DAY:
            continue
        if p.name in (EXPORTS, STAGING):  # le contenu est temporaire : on retire ce qui est resté plus d'un jour
            for child in p.iterdir():
                if now - newest_mtime(child) > DAY and not dry_run:
                    shutil.rmtree(child, ignore_errors=True)
        elif p.name not in known and p.name not in protected:
            report.orphans.append(p.name)
            total -= dir_size(p)
            if not dry_run:
                shutil.rmtree(p, ignore_errors=True)

    if not dry_run:
        store.mark_purged(report.deleted)
    report.remaining_bytes = max(total, 0)
    if report.deleted or report.orphans:
        log.info(
            "purge : %d run(s) et %d dossier(s) orphelin(s), %d Mo libérés",
            len(report.deleted),
            len(report.orphans),
            report.freed_bytes // 2**20,
        )
    return report
