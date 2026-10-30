"""ListFiles : liste les fichiers d'un dossier (ou d'un fichier liste), une entité par fichier."""

from __future__ import annotations

import csv
import fnmatch
import hashlib
import os
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from stat import S_ISDIR
from typing import Any, Literal

import pyarrow as pa
from pydantic import Field

from etl_engine import paths
from etl_engine.sdk import ConfigError, Emit, FilePath, Modifier, Params

SCHEMA = pa.schema(
    [
        ("path", pa.string()),
        ("dir", pa.string()),
        ("name", pa.string()),
        ("stem", pa.string()),
        ("ext", pa.string()),
        ("size", pa.int64()),
        ("mtime", pa.timestamp("s")),
        ("is_dir", pa.bool_()),
    ]
)


class ListFiles(Modifier):
    name = "ListFiles"
    label = "Lister des fichiers"
    category = "Lots et variables"
    description = "Une entité par fichier d'un dossier (ou d'un fichier liste) : chemin, nom, taille, date."
    help = (
        "Sert à traiter des dossiers : relier à « Lire les fichiers listés » (ReadListedFiles) pour lire chacun, ou à "
        "« Lancer un flux » (RunFlow) pour exécuter un flux par fichier. Attributs : path (chemin complet), dir, "
        "name (avec extension), stem (sans extension), ext (avec le point, en minuscules), size (octets), mtime "
        "(dernière modification), is_dir. Les motifs sont ceux du shell (*.shp, commune_??.gpkg) et s'appliquent au "
        "nom ; plusieurs motifs s'additionnent. Un fichier liste (.txt) contient un chemin par ligne ; un CSV, une "
        "colonne « path » (ou, à défaut, la première colonne)."
    )
    inputs = ()
    rejected = False

    class Params(Params):
        source: Literal["folder", "list_file"] = Field(
            "folder", title="Source", description="folder : un dossier ; list_file : un fichier qui liste les chemins."
        )
        folder: FilePath = Field(
            "",
            title="Dossier",
            description="Dossier à parcourir (source folder).",
            examples=["D:\\donnees\\shp"],
        )
        patterns: list[str] = Field(
            default_factory=lambda: ["*"],
            title="Motifs",
            description="Motifs de nom de fichier, ex. *.shp ; vide = tous les fichiers.",
            json_schema_extra={"default": ["*"]},
        )
        recursive: bool = Field(
            False, title="Parcourir les sous-dossiers", description="Descend dans les sous-dossiers."
        )
        include_dirs: bool = Field(
            False, title="Lister aussi les dossiers", description="Ajoute une entité par dossier (is_dir = vrai)."
        )
        list_file: FilePath = Field(
            "", title="Fichier liste", description="Fichier .txt (un chemin par ligne) ou .csv (colonne path)."
        )
        missing: Literal["error", "skip"] = Field(
            "error",
            title="Fichier introuvable dans la liste",
            description="error : arrête le flux ; skip : l'ignore avec un avertissement.",
        )
        sort: Literal["path", "mtime", "size"] = Field(
            "path", title="Trier par", description="Ordre des entités : chemin, date de modification ou taille."
        )

    # --- lecture de la liste --------------------------------------------------------------
    def _walk(self) -> Iterator[Path]:
        p = self.params
        if not p.folder:
            raise ConfigError("dossier non renseigné")
        root = Path(p.folder)
        paths.check(str(root), "read")
        if not root.is_dir():
            raise ConfigError(f"« {root} » n'est pas un dossier")
        wanted = [pattern for pattern in p.patterns if pattern.strip()] or ["*"]
        if p.recursive:
            iterator = (Path(dirpath) / name for dirpath, dirs, files in os.walk(root) for name in [*dirs, *files])
        else:
            iterator = (child for child in root.iterdir())
        for item in iterator:
            if any(fnmatch.fnmatch(item.name.lower(), pattern.lower()) for pattern in wanted):
                if item.is_dir() and not p.include_dirs:
                    continue
                yield item

    def _from_list_file(self) -> Iterator[Path]:
        p = self.params
        if not p.list_file:
            raise ConfigError("fichier liste non renseigné")
        source = Path(p.list_file)
        paths.check(str(source), "read")
        if not source.is_file():
            raise ConfigError(f"fichier introuvable : {source}")
        text = source.read_text(encoding="utf-8-sig")
        if source.suffix.lower() == ".csv":
            sample = text[:4096]
            delimiter = ";" if sample.count(";") > sample.count(",") else ","
            rows = list(csv.reader(text.splitlines(), delimiter=delimiter))
            if not rows:
                return
            head = [h.strip().lower() for h in rows[0]]
            column = head.index("path") if "path" in head else 0
            values = [r[column].strip() for r in rows[1:] if len(r) > column]
        else:
            values = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
        for value in values:
            yield Path(value)

    def _entries(self) -> list[tuple[Path, os.stat_result]]:
        found: list[tuple[Path, os.stat_result]] = []
        listed = self._from_list_file() if self.params.source == "list_file" else self._walk()
        for item in listed:
            try:
                found.append((item, item.stat()))
            except OSError:
                if self.params.source == "list_file" and self.params.missing == "error":
                    raise ConfigError(f"fichier introuvable : {item}") from None
                self.ctx.warning(f"fichier introuvable, ignoré : {item}")
        key = {
            "path": lambda e: str(e[0]).lower(),
            "mtime": lambda e: e[1].st_mtime,
            "size": lambda e: e[1].st_size,
        }[self.params.sort]
        return sorted(found, key=key)

    # --- modifier -------------------------------------------------------------------------
    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        if self.params.source == "folder" and not self.params.folder:
            raise ConfigError("dossier non renseigné")
        if self.params.source == "list_file" and not self.params.list_file:
            raise ConfigError("fichier liste non renseigné")
        return {"output": SCHEMA}

    def fingerprint_extra(self) -> str:
        """Le résultat change quand les fichiers changent : le cache ne doit pas servir une liste périmée."""
        digest = hashlib.sha1(usedforsecurity=False)
        try:
            self._listed = self._entries()  # gardée pour le run : le dossier n'est parcouru qu'une fois
            for path, stat in self._listed:
                digest.update(f"{path}|{stat.st_size}|{stat.st_mtime_ns}\n".encode())
        except ConfigError:
            return "erreur"
        return digest.hexdigest()

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        entries = getattr(self, "_listed", None)
        if entries is None:
            entries = self._entries()
        for start in range(0, len(entries), self.ctx.batch_rows):
            self.ctx.check_cancel()
            chunk = entries[start : start + self.ctx.batch_rows]
            emit(
                "output",
                pa.table(
                    {
                        "path": [str(p) for p, _ in chunk],
                        "dir": [str(p.parent) for p, _ in chunk],
                        "name": [p.name for p, _ in chunk],
                        "stem": [p.stem for p, _ in chunk],
                        "ext": [p.suffix.lower() for p, _ in chunk],
                        "size": pa.array([0 if S_ISDIR(s.st_mode) else s.st_size for _, s in chunk], pa.int64()),
                        "mtime": pa.array([datetime.fromtimestamp(s.st_mtime) for _, s in chunk], pa.timestamp("s")),
                        "is_dir": [S_ISDIR(s.st_mode) for _, s in chunk],
                    },
                    schema=SCHEMA,
                ),
            )
        self.ctx.info(f"{len(entries)} fichier(s) listé(s)")
