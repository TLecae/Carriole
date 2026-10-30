"""Lecture d'un fichier ((Geo)Parquet, GeoPackage, CSV, …)."""

from __future__ import annotations

import glob
import os
from collections.abc import Mapping
from typing import Any, Literal

import pyarrow as pa
from pydantic import Field

from etl_engine import formats, paths
from etl_engine.sdk import ConfigError, Crs, Emit, FilePath, Modifier, Params

Format = Literal["auto", "parquet", "gpkg", "csv", "geojson", "fgb", "shp", "tab", "mif", "xlsx"]


class Reader(Modifier):
    name = "Reader"
    label = "Lecture de fichier"
    category = "Lecture"
    hidden = True  # remplacé par un lecteur par format (file_io) ; les flux sont convertis à l'ouverture
    description = "Lit un fichier vecteur ou tabulaire et envoie ses entités."
    help = (
        "Le format est déduit de l'extension. Pour un GeoPackage ou MapInfo, indiquer la couche ; pour Excel, la "
        "feuille (et au besoin la plage, ex. A1:F200). Un motif de fichiers (dossier/**/*.gpkg) lit tous les "
        "fichiers trouvés, leurs schémas doivent être compatibles. "
        "Pour un CSV : les colonnes sont lues comme du texte (les codes à zéro initial sont conservés) ; "
        "indiquer la colonne WKT ou les colonnes X/Y pour obtenir une géométrie."
    )
    inputs = ()
    outputs = ("output",)
    rejected = False

    class Params(Params):
        path: FilePath = Field(
            "",
            title="Fichier",
            description="Chemin du fichier ; motifs acceptés (*.csv, **/*.gpkg).",
            examples=["D:/donnees/communes.gpkg"],
        )
        paths: list[FilePath] = Field(
            default_factory=list,
            title="Autres fichiers",
            description="Fichiers ou motifs lus en plus de « Fichier » (leurs schémas doivent être compatibles).",
        )
        format: Format = Field("auto", title="Format", description="Déduit de l'extension si « auto ».")
        layer: str = Field(
            "",
            title="Couche / feuille",
            description="GeoPackage, MapInfo… : nom de la couche ; Excel : nom de la feuille (vide = la première).",
            examples=["communes"],
        )
        cell_range: str = Field(
            "", title="Plage (Excel)", description="Ex. A1:F200 ; la première ligne = en-têtes.", examples=["A1:F200"]
        )
        encoding: str = Field("", title="Encodage", description="Vide = détection automatique (utf-8, sinon latin-1).")
        where: str = Field(
            "",
            title="Filtre attributaire",
            description="Condition SQL appliquée à la lecture (formats GDAL).",
            examples=["population > 1000"],
        )
        delimiter: str = Field(
            "", title="Séparateur (CSV)", description="Vide = détection automatique.", examples=[";"]
        )
        header: bool = Field(
            True,
            title="Première ligne = en-têtes (CSV, Excel)",
            description="Décocher si le fichier n'a pas de ligne d'en-têtes.",
        )
        detect_types: bool = Field(
            False,
            title="Détecter les types (CSV)",
            description="Sinon tout est lu en texte : les codes à zéro initial sont conservés.",
        )
        add_filename: bool = Field(
            False,
            title="Ajouter le nom du fichier",
            description="Ajoute la colonne « filename » (utile avec un motif de fichiers).",
        )
        wkt_column: str = Field(
            "", title="Colonne WKT (CSV)", description="Colonne contenant la géométrie en WKT.", examples=["wkt"]
        )
        x_column: str = Field(
            "",
            title="Colonne X (CSV)",
            description="Colonne des X (ou longitudes) ; à renseigner avec la colonne Y pour créer des points.",
            examples=["x"],
        )
        y_column: str = Field(
            "",
            title="Colonne Y (CSV)",
            description="Colonne des Y (ou latitudes) ; à renseigner avec la colonne X pour créer des points.",
            examples=["y"],
        )
        crs: Crs = Field(
            "", title="Système de coordonnées (CSV)", description="Celui de la colonne WKT ou des colonnes X/Y."
        )

    def _patterns(self) -> list[str]:
        """Les fichiers ou motifs à lire : « Fichier », puis « Autres fichiers »."""
        return [x for x in [self.params.path, *self.params.paths] if x]

    def _fmt(self) -> str:
        patterns = self._patterns()
        if not patterns:
            raise ConfigError("chemin du fichier non renseigné")
        return self.params.format if self.params.format != "auto" else formats.detect(patterns[0])

    def _options(self) -> dict[str, Any]:
        p = self.params
        return {
            "layer": p.layer or None,
            "cell_range": p.cell_range or None,
            "encoding": p.encoding or None,
            "where": p.where or None,
            "delimiter": p.delimiter or None,
            "header": p.header,
            "detect_types": p.detect_types,
            "add_filename": p.add_filename,
            "wkt_column": p.wkt_column or None,
            "x_column": p.x_column or None,
            "y_column": p.y_column or None,
            "crs": p.crs or None,
        }

    def _files(self) -> list[str]:
        files: list[str] = []
        for pattern in self._patterns():
            found = sorted(glob.glob(pattern, recursive=True))
            files.extend(found)
        files = list(dict.fromkeys(files))
        for f in files:
            paths.check(f, "read")  # dossiers autorisés (ETL_READ_ROOTS)
        return files

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        fmt = self._fmt()
        files = self._files()
        if not files:
            return {"output": None}
        try:
            schema = formats.read_schema(fmt, files[0] if fmt != "csv" else self._patterns()[0], **self._options())
            if self.params.add_filename and fmt != "csv" and "filename" not in schema.names:
                schema = schema.append(pa.field("filename", pa.string()))
            return {"output": schema}
        except Exception:  # noqa: BLE001 - schéma inconnu : l'éditeur propose simplement moins d'attributs
            return {"output": None}

    def fingerprint_extra(self) -> str:
        return "|".join(f"{f}:{os.stat(f).st_mtime_ns}:{os.stat(f).st_size}" for f in self._files())

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        fmt = self._fmt()
        files = self._files()
        for pattern in self._patterns():
            if not glob.glob(pattern, recursive=True):
                raise ConfigError(f"fichier introuvable : {pattern}")
        opts = self._options()
        # Le CSV gère lui-même les motifs de fichiers (union par nom de colonne).
        sources = self._patterns() if fmt == "csv" else files
        total = 0
        for src in sources:
            for batch in formats.read_batches(fmt, src, self.ctx.batch_rows, **opts):
                if self.params.add_filename and fmt != "csv" and "filename" not in batch.column_names:
                    batch = batch.append_column(
                        "filename", pa.repeat(pa.scalar(os.path.basename(src), pa.string()), batch.num_rows)
                    )
                self.ctx.check_cancel()
                total += batch.num_rows
                self.ctx.progress(total)
                emit("output", batch)
        self.ctx.info(f"{total} entités lues")
