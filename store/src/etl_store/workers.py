"""Identité des workers inscrite sur leurs runs, et vérification qu'un worker de ce poste tourne encore."""

from __future__ import annotations

import os
import socket

import psutil


def worker_name() -> str:
    """Nom du worker inscrit sur ses runs : poste et numéro de processus."""
    return f"{socket.gethostname()}-{os.getpid()}"


def alive_here(worker: str | None, since: float | None) -> bool:
    """Vrai si `worker` (voir `worker_name`) est un processus encore en vie sur ce poste, lancé avant `since` (début du
    run) : c'est bien lui qui a pris le run, pas un autre programme qui aurait repris son numéro.

    Son run peut ne plus avoir de heartbeat récent sans être orphelin : un calcul qui garde le GIL
    (`shapely.polygonize`…) bloque le fil du heartbeat sans arrêter le worker. Un worker d'un autre poste ne se vérifie
    pas : son heartbeat seul fait foi."""
    host, _, pid = (worker or "").rpartition("-")
    if host != socket.gethostname() or not pid.isdigit():
        return False
    try:
        return psutil.Process(int(pid)).create_time() <= (since or 0.0)
    except psutil.Error:
        return False
