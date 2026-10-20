"""Écriture de fichier « tout ou rien » : un plantage ou un partage qui tombe ne laisse jamais un fichier tronqué."""

from __future__ import annotations

import contextlib
import os
import sys
import uuid
from pathlib import Path


def write_text(path: str | Path, text: str, create_only: bool = False) -> None:
    """Écrit `text` (UTF-8, fins de ligne LF) dans `path` d'un seul bloc.

    Le contenu est d'abord écrit dans un fichier voisin `.part` puis substitué au fichier cible (`os.replace`).
    `create_only=True` : échoue avec `FileExistsError` si le fichier existe déjà (la création est elle aussi
    atomique : deux utilisateurs qui créent le même fichier, un seul réussit).
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.part")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if not create_only:
            os.replace(tmp, target)
        elif sys.platform == "win32":
            os.rename(tmp, target)  # Windows : refuse d'écraser (FileExistsError)
        else:
            os.link(tmp, target)  # POSIX : refuse d'écraser ; le lien dur évite la course d'un simple test d'existence
            tmp.unlink()
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


def part_name(path: str | Path) -> str:
    """Nom caché et unique du fichier de travail d'une écriture « tout ou rien »."""
    target = Path(path)
    return str(target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.part"))
