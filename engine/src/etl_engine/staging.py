"""Écriture en deux temps sur les partages réseau : on écrit en local, puis on publie le fichier d'un bloc.

Écrire directement sur un partage (SMB/NFS) pose trois problèmes :
- **SQLite** (GeoPackage) y fait beaucoup de petites écritures avec verrous : lent, fragile si la liaison tressaille ;
- un flux **interrompu** (erreur, annulation, serveur redémarré) laisserait un fichier à moitié écrit, que les
  collègues ouvriraient ;
- un fichier **remplacé** est visible dans un état intermédiaire.

Pour les partages réseau de Windows (chemin UNC ou lecteur mappé) et les dossiers listés dans
`ETL_STAGED_ROOTS`, le `Writer` écrit donc dans un dossier de travail local (`ETL_STAGING_DIR`, par défaut
`<cache>/_staging`), puis `publish()` copie chaque fichier sous un nom temporaire caché
**dans le dossier de destination**, force son écriture sur disque et le renomme (`os.replace` : atomique dans un même
dossier). Tant que ce n'est pas fait, la destination est intacte ; en cas d'échec, elle le reste et le travail local
est supprimé. Quand le fichier existe déjà et que le mode le conserve (ajout, couche d'un GeoPackage, feuille Excel), il
est d'abord copié en local.

Limites : la publication d'un Shapefile ou d'un fichier MapInfo (plusieurs fichiers) renomme les fichiers l'un après
l'autre, pas en bloc ; un dossier partitionné (GeoParquet) n'est pas concerné ; deux flux qui modifient *en même temps*
le même GeoPackage s'écrasent (le dernier publié l'emporte), comme avec deux copies du fichier.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import uuid
from pathlib import Path

from etl_engine.sdk import ConfigError

# Formats dont un « fichier » est un groupe de fichiers de même nom (data.shp, data.dbf, data.shx, data.prj…), avec les
# extensions qui en font partie : on ne touche jamais à un autre fichier de même nom (data.csv à côté de data.shp).
FAMILIES = {
    "shp": {".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx", ".xml"},
    "tab": {".tab", ".dat", ".map", ".id", ".ind"},
    "mif": {".mif", ".mid"},
}
# Formats où « overwrite » remplace une couche ou une feuille dans un fichier qui garde ses autres éléments
KEEPS_EXISTING = {"gpkg", "xlsx", "sqlite"}
CHUNK = 8 * 2**20


def staged_roots() -> list[Path]:
    raw = os.environ.get("ETL_STAGED_ROOTS", "").strip()
    return [Path(p).resolve() for p in raw.split(os.pathsep) if p.strip()]


def needs_staging(path: str | os.PathLike[str]) -> bool:
    """Vrai pour un dossier de `ETL_STAGED_ROOTS`, et sous Windows pour tout partage réseau (UNC ou lecteur mappé)."""
    from etl_engine import drives

    if drives.is_network_path(path):
        return True
    resolved = Path(path).resolve()
    return any(resolved == r or r in resolved.parents for r in staged_roots())


def scratch_root() -> Path:
    explicit = os.environ.get("ETL_STAGING_DIR", "").strip()
    if explicit:
        return Path(explicit)
    cache = os.environ.get("ETL_CACHE_DIR", "").strip()
    return (Path(cache) if cache else Path(tempfile.gettempdir())) / "_staging"


def _siblings(final: Path, fmt: str) -> list[Path]:
    """Les fichiers du groupe de `final` (`data.shp`, `data.dbf`…) qui existent déjà."""
    wanted = FAMILIES[fmt]
    return [p for p in final.parent.glob(f"{_escape(final.stem)}.*") if p.is_file() and p.suffix.lower() in wanted]


def _escape(name: str) -> str:
    return "".join(f"[{c}]" if c in "*?[" else c for c in name)


def _copy_atomically(source: Path, destination: Path) -> None:
    """Copie sous un nom caché du dossier de destination, force l'écriture sur disque, puis renomme."""
    part = destination.with_name(f".{destination.name}.{uuid.uuid4().hex[:8]}.part")
    try:
        with open(source, "rb") as src, open(part, "wb") as dst:
            shutil.copyfileobj(src, dst, CHUNK)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(part, destination)
    except BaseException:
        with contextlib.suppress(OSError):
            part.unlink()
        raise


class StagedFile:
    """Un fichier de sortie écrit en local (`work_path`) puis publié à son emplacement final."""

    def __init__(self, final: str | os.PathLike[str], fmt: str, mode: str) -> None:
        self.final = Path(final)
        self.fmt = fmt
        self.mode = mode
        if mode == "create" and self._existing():
            raise ConfigError(f"le fichier existe déjà : {final}")
        self.workdir = scratch_root() / uuid.uuid4().hex
        self.workdir.mkdir(parents=True)
        self.work_path = str(self.workdir / self.final.name)
        try:
            if self._keeps_existing():
                for existing in self._existing():
                    shutil.copyfile(existing, self.workdir / existing.name)
        except BaseException:
            self.discard()
            raise

    def _existing(self) -> list[Path]:
        if self.fmt in FAMILIES:
            return _siblings(self.final, self.fmt)
        return [self.final] if self.final.is_file() else []

    def _keeps_existing(self) -> bool:
        return self.mode == "append" or (self.mode == "overwrite" and self.fmt in KEEPS_EXISTING)

    def publish(self) -> list[str]:
        """Remplace la destination par le travail local, fichier par fichier ; renvoie les fichiers publiés."""
        produced = sorted(p for p in self.workdir.iterdir() if p.is_file())
        if self.fmt in FAMILIES:
            produced = [p for p in produced if p.suffix.lower() in FAMILIES[self.fmt]]
        else:
            produced = [p for p in produced if p.name == self.final.name]
        if not produced:
            return []
        self.final.parent.mkdir(parents=True, exist_ok=True)
        done: list[str] = []
        for p in produced:
            _copy_atomically(p, self.final.parent / p.name)
            done.append(p.name)
        if self.fmt in FAMILIES and self.mode == "overwrite":
            for old in self._existing():  # un ancien .cpg ou .qix qui ne correspond plus aux nouvelles données
                if old.name not in done:
                    with contextlib.suppress(OSError):
                        old.unlink()
        return done

    def discard(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)


def begin(final: str | os.PathLike[str], fmt: str, mode: str) -> StagedFile | None:
    """Prépare l'écriture en deux temps si `final` est sur un partage réseau, sinon `None`."""
    return StagedFile(final, fmt, mode) if needs_staging(final) else None
