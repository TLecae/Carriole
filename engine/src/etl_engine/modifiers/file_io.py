"""Un lecteur et un écrivain par format de fichier : chacun n'a que les réglages de son format.

`FileReader` et `FileWriter` portent la mécanique commune (motifs de fichiers, schéma, lots, écriture locale puis
publiée sur un partage) ; chaque format déclare ses extensions et ses paramètres. Les anciens « Lecture de fichier »
et « Écriture de fichier », qui mélangeaient les réglages de tous les formats, restent lisibles : `flow.py` les
convertit à l'ouverture d'un flux (`convert_legacy_node`).
"""

from __future__ import annotations

import glob
import os
from collections.abc import Iterator, Mapping
from typing import Any, ClassVar, Literal

import pyarrow as pa
from pydantic import Field

from etl_engine import formats, paths
from etl_engine.modifiers.writer import abort_target, finish_target, open_target
from etl_engine.sdk import AttributeList, ConfigError, Crs, Emit, FilePath, Modifier, Params, first_rows, row_limit

# réglages que les modules de formats savent lire (nom du paramètre -> option de lecture)
READ_OPTIONS = (
    "layer", "cell_range", "encoding", "where", "delimiter", "header", "detect_types", "add_filename",
    "wkt_column", "x_column", "y_column", "crs", "sql",
)  # fmt: skip


def path_field(extensions: tuple[str, ...], example: str, access: Literal["read", "write"] = "read") -> Any:
    """Champ « Fichier » : l'explorateur ne montre que les fichiers de ces extensions."""
    extra: dict[str, Any] = {"x-etl-extensions": list(extensions)}
    if access == "write":
        extra["x-etl-access"] = "write"
    if access == "read":
        description = "Chemin du fichier ; motifs acceptés (*.ext, dossier/**/*.ext) : tous les fichiers trouvés."
    else:
        description = "Chemin du fichier à écrire (dossiers créés si besoin)."
    return Field(
        "",
        title="Fichier",
        description=description,
        examples=[example],
        json_schema_extra={"x-etl-widget": "path", **extra},
    )


def other_files() -> Any:
    return Field(
        default_factory=list,
        title="Autres fichiers",
        description="Fichiers ou motifs lus en plus de « Fichier » (leurs schémas doivent être compatibles).",
    )


def filename_column() -> Any:
    return Field(
        False,
        title="Ajouter le nom du fichier",
        description="Ajoute la colonne « filename » (utile avec un motif de fichiers).",
    )


def where() -> Any:
    return Field(
        "",
        title="Filtre attributaire",
        description="Condition SQL appliquée à la lecture ; vide = toutes les entités.",
        examples=["population > 1000"],
    )


def encoding() -> Any:
    return Field("", title="Encodage", description="Vide = détection automatique (utf-8, sinon latin-1).")


def mode(layer: bool = False) -> Any:
    if layer:
        text = (
            "create : échoue si la couche existe ; overwrite : remplace la couche (les autres couches du fichier "
            "restent) ; append : ajoute."
        )
    else:
        text = "create : échoue si le fichier existe ; overwrite : le remplace ; append : ajoute."
    return Field("overwrite", title="Mode", description=text)


# ------------------------------------------------------------------------------------------------ lecture


class FileReader(Modifier):
    """Lecteur d'un format de fichier (classe de base : sans nom, elle n'est pas dans la palette)."""

    category = "Lecture"
    inputs = ()
    outputs = ("output",)
    rejected = False
    formats: ClassVar[tuple[str, ...]] = ()  # formats de `etl_engine.formats` (le premier : par défaut)

    def _patterns(self) -> list[str]:
        return [x for x in [getattr(self.params, "path", ""), *getattr(self.params, "paths", [])] if x]

    def _fmt(self) -> str:
        patterns = self._patterns()
        if not patterns:
            raise ConfigError("chemin du fichier non renseigné")
        try:
            found = formats.detect(patterns[0])
        except ConfigError:
            return self.formats[0]
        return found if found in self.formats else self.formats[0]

    def _options(self) -> dict[str, Any]:
        p = self.params
        out: dict[str, Any] = {}
        for name in READ_OPTIONS:
            value = getattr(p, "sheet" if name == "layer" and hasattr(p, "sheet") else name, None)
            out[name] = value if isinstance(value, bool) else (value or None)
        out["header"] = getattr(p, "header", True)
        return out

    def _files(self) -> list[str]:
        files: list[str] = []
        for pattern in self._patterns():
            found = sorted(glob.glob(pattern, recursive=True))
            files.extend(f for f in found if f not in files)
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
            if getattr(self.params, "add_filename", False) and fmt != "csv" and "filename" not in schema.names:
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
        sources = self._patterns() if fmt == "csv" else files  # le CSV gère lui-même les motifs (union par nom)
        add_filename = getattr(self.params, "add_filename", False) and fmt != "csv"

        def batches() -> Iterator[pa.Table]:
            for src in sources:
                for batch in formats.read_batches(fmt, src, self.ctx.batch_rows, **opts):
                    if add_filename and "filename" not in batch.column_names:
                        batch = batch.append_column("filename", pa.array([os.path.basename(src)] * batch.num_rows))
                    yield batch

        total = 0
        for batch in first_rows(batches(), self.params.max_rows):
            self.ctx.check_cancel()
            total += batch.num_rows
            self.ctx.progress(total)
            emit("output", batch)
        self.ctx.info(f"{total} entités lues")


class CsvReader(FileReader):
    name = "CsvReader"
    label = "Lecture CSV"
    description = "Lit un fichier CSV (ou texte délimité) ; une colonne WKT ou des colonnes X/Y donnent la géométrie."
    help = (
        "Les colonnes sont lues comme du texte (les codes à zéro initial sont conservés), sauf si « Détecter les "
        "types » est coché. Indiquer la colonne WKT, ou les colonnes X et Y, pour obtenir une géométrie, avec son "
        "système de coordonnées. Un motif (dossier/*.csv) lit tous les fichiers : leurs colonnes sont réunies par nom."
    )
    formats = ("csv",)

    class Params(Params):
        path: str = path_field((".csv", ".txt", ".tsv"), "D:/donnees/communes.csv")
        delimiter: str = Field("", title="Séparateur", description="Vide = détection automatique.", examples=[";"])
        encoding: str = encoding()
        header: bool = Field(
            True, title="Première ligne = en-têtes", description="Décocher si le fichier n'a pas de ligne d'en-têtes."
        )
        detect_types: bool = Field(
            False,
            title="Détecter les types",
            description="Sinon tout est lu en texte : les codes à zéro initial sont conservés.",
        )
        wkt_column: str = Field(
            "", title="Colonne WKT", description="Colonne contenant la géométrie en WKT.", examples=["wkt"]
        )
        x_column: str = Field(
            "", title="Colonne X", description="Colonne des X (ou longitudes), avec la colonne Y.", examples=["x"]
        )
        y_column: str = Field(
            "", title="Colonne Y", description="Colonne des Y (ou latitudes), avec la colonne X.", examples=["y"]
        )
        crs: Crs = Field("", title="Système de coordonnées", description="Celui de la colonne WKT ou des X/Y.")
        paths: list[FilePath] = other_files()
        add_filename: bool = filename_column()
        max_rows: int = row_limit()


class ExcelReader(FileReader):
    name = "ExcelReader"
    label = "Lecture Excel"
    description = "Lit une feuille d'un classeur Excel (.xlsx)."
    help = "La première ligne de la plage donne les noms de colonnes (sauf si décoché). Vide : la première feuille."
    formats = ("xlsx",)

    class Params(Params):
        path: str = path_field((".xlsx", ".xlsm"), "D:/donnees/communes.xlsx")
        sheet: str = Field(
            "", title="Feuille", description="Nom de la feuille ; vide = la première.", examples=["2024"]
        )
        cell_range: str = Field(
            "", title="Plage", description="Ex. A1:F200 ; vide = toute la feuille.", examples=["A1:F200"]
        )
        header: bool = Field(
            True, title="Première ligne = en-têtes", description="Décocher si la plage n'a pas de ligne d'en-têtes."
        )
        paths: list[FilePath] = other_files()
        add_filename: bool = filename_column()
        max_rows: int = row_limit()


class GeoPackageReader(FileReader):
    name = "GeoPackageReader"
    label = "Lecture GeoPackage"
    description = "Lit une couche d'un GeoPackage (.gpkg)."
    help = "Indiquer la couche si le GeoPackage en contient plusieurs (vide : la première)."
    formats = ("gpkg",)

    class Params(Params):
        path: str = path_field((".gpkg",), "D:/donnees/communes.gpkg")
        layer: str = Field(
            "", title="Couche", description="Nom de la couche ; vide = la première.", examples=["communes"]
        )
        where: str = where()
        paths: list[FilePath] = other_files()
        add_filename: bool = filename_column()
        max_rows: int = row_limit()


class ShapefileReader(FileReader):
    name = "ShapefileReader"
    label = "Lecture Shapefile"
    description = "Lit un Shapefile (.shp et ses fichiers compagnons)."
    help = "L'encodage est lu dans le fichier .cpg s'il existe ; sinon utf-8, puis latin-1."
    formats = ("shp",)

    class Params(Params):
        path: str = path_field((".shp",), "D:/donnees/communes.shp")
        encoding: str = encoding()
        where: str = where()
        paths: list[FilePath] = other_files()
        add_filename: bool = filename_column()
        max_rows: int = row_limit()


class GeoJsonReader(FileReader):
    name = "GeoJsonReader"
    label = "Lecture GeoJSON"
    description = "Lit un fichier GeoJSON."
    help = (
        "Le GeoJSON est en WGS 84 (EPSG:4326) selon la norme ; un autre système déclaré dans le fichier est respecté."
    )
    formats = ("geojson",)

    class Params(Params):
        path: str = path_field((".geojson", ".json"), "D:/donnees/communes.geojson")
        where: str = where()
        paths: list[FilePath] = other_files()
        add_filename: bool = filename_column()
        max_rows: int = row_limit()


class FlatGeobufReader(FileReader):
    name = "FlatGeobufReader"
    label = "Lecture FlatGeobuf"
    description = "Lit un fichier FlatGeobuf (.fgb)."
    help = "Format vecteur compact et indexé, rapide à lire."
    formats = ("fgb",)

    class Params(Params):
        path: str = path_field((".fgb",), "D:/donnees/communes.fgb")
        where: str = where()
        paths: list[FilePath] = other_files()
        add_filename: bool = filename_column()
        max_rows: int = row_limit()


class GeoParquetReader(FileReader):
    name = "GeoParquetReader"
    label = "Lecture GeoParquet"
    description = "Lit un fichier Parquet ou GeoParquet."
    help = "La géométrie et son système de coordonnées sont lus dans les métadonnées GeoParquet."
    formats = ("parquet",)

    class Params(Params):
        path: str = path_field((".parquet", ".geoparquet"), "D:/donnees/communes.parquet")
        paths: list[FilePath] = other_files()
        add_filename: bool = filename_column()
        max_rows: int = row_limit()


class MapInfoReader(FileReader):
    name = "MapInfoReader"
    label = "Lecture MapInfo"
    description = "Lit une table MapInfo (.tab) ou un fichier MIF/MID (.mif)."
    help = "Le format (TAB ou MIF) est déduit de l'extension."
    formats = ("tab", "mif")

    class Params(Params):
        path: str = path_field((".tab", ".mif"), "D:/donnees/communes.tab")
        encoding: str = encoding()
        where: str = where()
        max_rows: int = row_limit()

        # ------------------------------------------------------------------------------------------------ écriture
        paths: list[FilePath] = other_files()
        add_filename: bool = filename_column()


class FileWriter(Modifier):
    """Écrivain d'un format de fichier (classe de base : sans nom, elle n'est pas dans la palette)."""

    category = "Écriture"
    inputs = ("input",)
    outputs = ()
    rejected = False
    stateful = True
    formats: ClassVar[tuple[str, ...]] = ()

    def _fmt(self, path: str) -> str:
        try:
            found = formats.detect(path)
        except ConfigError:
            return self.formats[0]
        return found if found in self.formats else self.formats[0]

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        p = self.params
        if not p.path:
            raise ConfigError("chemin du fichier non renseigné")
        writer, stage = open_target(p, p.path, self._fmt(p.path))
        rows = 0
        try:

            def counted() -> Iterator[Any]:
                nonlocal rows
                for batch in inputs.get("input", ()):
                    self.ctx.check_cancel()
                    rows += batch.num_rows
                    yield batch

            if hasattr(writer, "write_stream"):
                writer.write_stream(counted())
            else:
                for batch in counted():
                    writer.write(batch)
        except BaseException:
            abort_target(writer, stage)
            raise
        finish_target(writer, stage, self.ctx)
        if rows == 0:
            self.ctx.warning("aucune entité reçue : rien n'a été écrit")
        else:
            self.ctx.info(f"{rows} entités écrites dans {p.path}")


SHARE = (
    "Sur un partage réseau, le fichier est écrit en local puis publié d'un bloc : une erreur ou un arrêt en cours de "
    "route laisse le fichier existant intact."
)


class CsvWriter(FileWriter):
    name = "CsvWriter"
    label = "Écriture CSV"
    description = "Écrit les entités dans un fichier CSV (la géométrie en WKT)."
    help = "Encodage utf-8. " + SHARE
    formats = ("csv",)

    class Params(Params):
        path: str = path_field((".csv", ".txt", ".tsv"), "D:/sorties/resultat.csv", "write")
        delimiter: str = Field(",", title="Séparateur", description="Séparateur de colonnes.", examples=[";"])
        mode: Literal["create", "overwrite", "append"] = mode()


class ExcelWriter(FileWriter):
    name = "ExcelWriter"
    label = "Écriture Excel"
    description = "Écrit les entités dans une feuille d'un classeur Excel (.xlsx)."
    help = (
        "Plusieurs écritures Excel peuvent viser des feuilles différentes du même classeur : en mode « overwrite », "
        "seule la feuille visée est remplacée. " + SHARE
    )
    formats = ("xlsx",)

    class Params(Params):
        path: str = path_field((".xlsx",), "D:/sorties/resultat.xlsx", "write")
        sheet: str = Field("", title="Feuille", description="Nom de la feuille ; vide = « Feuil1 ».", examples=["2024"])
        mode: Literal["create", "overwrite", "append"] = mode(layer=True)


class GeoPackageWriter(FileWriter):
    name = "GeoPackageWriter"
    label = "Écriture GeoPackage"
    description = "Écrit les entités dans une couche d'un GeoPackage (.gpkg)."
    help = (
        "Plusieurs écritures GeoPackage peuvent viser des couches différentes du même fichier : en mode "
        "« overwrite », seule la couche visée est remplacée. " + SHARE
    )
    formats = ("gpkg",)

    class Params(Params):
        path: str = path_field((".gpkg",), "D:/sorties/resultat.gpkg", "write")
        layer: str = Field(
            "", title="Couche", description="Nom de la couche ; vide = le nom du fichier.", examples=["communes"]
        )
        mode: Literal["create", "overwrite", "append"] = mode(layer=True)


class ShapefileWriter(FileWriter):
    name = "ShapefileWriter"
    label = "Écriture Shapefile"
    description = "Écrit les entités dans un Shapefile (.shp)."
    help = (
        "Noms de colonnes limités à 10 caractères (les renommages sont journalisés), un seul type de géométrie, "
        "limite de 2 Go. Un fichier .cpg indique l'encodage. " + SHARE
    )
    formats = ("shp",)

    class Params(Params):
        path: str = path_field((".shp",), "D:/sorties/resultat.shp", "write")
        encoding: str = Field("", title="Encodage", description="Vide = utf-8 (écrit dans le .cpg).")
        mode: Literal["create", "overwrite", "append"] = mode()


class GeoJsonWriter(FileWriter):
    name = "GeoJsonWriter"
    label = "Écriture GeoJSON"
    description = "Écrit les entités dans un fichier GeoJSON."
    help = "Pour suivre la norme, reprojeter avant en WGS 84 (EPSG:4326). " + SHARE
    formats = ("geojson",)

    class Params(Params):
        path: str = path_field((".geojson", ".json"), "D:/sorties/resultat.geojson", "write")
        mode: Literal["create", "overwrite", "append"] = mode()


class FlatGeobufWriter(FileWriter):
    name = "FlatGeobufWriter"
    label = "Écriture FlatGeobuf"
    description = "Écrit les entités dans un fichier FlatGeobuf (.fgb), avec index spatial."
    help = SHARE
    formats = ("fgb",)

    class Params(Params):
        path: str = path_field((".fgb",), "D:/sorties/resultat.fgb", "write")
        mode: Literal["create", "overwrite", "append"] = mode()


class GeoParquetWriter(FileWriter):
    name = "GeoParquetWriter"
    label = "Écriture GeoParquet"
    description = "Écrit les entités dans un fichier GeoParquet (ou un dossier partitionné)."
    help = "« Partitionner par » écrit un sous-dossier par valeur : « Fichier » est alors un dossier. " + SHARE
    formats = ("parquet",)

    class Params(Params):
        path: str = path_field((".parquet", ".geoparquet"), "D:/sorties/resultat.parquet", "write")
        mode: Literal["create", "overwrite", "append"] = mode()
        compression: Literal["zstd", "snappy", "gzip", "none"] = Field(
            "zstd", title="Compression", description="zstd : compact et rapide ; none : aucune."
        )
        partition_by: AttributeList = Field(
            default_factory=list,
            title="Partitionner par",
            description="Colonnes dont chaque valeur donne un sous-dossier ; vide = un seul fichier.",
        )


class MapInfoWriter(FileWriter):
    name = "MapInfoWriter"
    label = "Écriture MapInfo"
    description = "Écrit les entités dans une table MapInfo (.tab) ou un fichier MIF/MID (.mif)."
    help = "Le format (TAB ou MIF) est déduit de l'extension. " + SHARE
    formats = ("tab", "mif")

    class Params(Params):
        path: str = path_field((".tab", ".mif"), "D:/sorties/resultat.tab", "write")
        encoding: str = Field("", title="Encodage", description="Vide = celui du format.")
        mode: Literal["create", "overwrite", "append"] = mode()


class SpatiaLiteReader(FileReader):
    name = "SpatiaLiteReader"
    label = "Lecture SQLite / SpatiaLite"
    description = "Lit une table d'une base SQLite ou SpatiaLite (.sqlite, .db), ou le résultat d'une requête SQL."
    help = (
        "Indiquer la table (vide : la première) **ou** une requête SQL (dialecte SQLite), qui la remplace. La "
        "géométrie SpatiaLite devient la géométrie du flux. Pour un GeoPackage, utiliser « Lecture GeoPackage »."
    )
    formats = ("sqlite",)

    class Params(Params):
        path: str = path_field((".sqlite", ".db", ".spatialite"), "D:/donnees/base.sqlite")
        layer: str = Field(
            "", title="Table", description="Nom de la table ; vide = la première.", examples=["communes"]
        )
        sql: str = Field(
            "",
            title="Requête SQL",
            description="Facultatif : remplace la table (dialecte SQLite) ; un seul SELECT.",
            examples=["SELECT nom, geometry FROM communes WHERE dep = '31'"],
        )
        where: str = where()
        paths: list[FilePath] = other_files()
        add_filename: bool = filename_column()
        max_rows: int = row_limit()


class SpatiaLiteWriter(FileWriter):
    name = "SpatiaLiteWriter"
    label = "Écriture SQLite / SpatiaLite"
    description = "Écrit les entités dans une table d'une base SpatiaLite (.sqlite), créée si besoin."
    help = (
        "Plusieurs écritures peuvent viser des tables différentes de la même base : en mode « overwrite », seule la "
        "table visée est remplacée. Une base créée par Carriole est une base SpatiaLite (lue par QGIS…). " + SHARE
    )
    formats = ("sqlite",)

    class Params(Params):
        path: str = path_field((".sqlite", ".db", ".spatialite"), "D:/sorties/resultat.sqlite", "write")
        layer: str = Field(
            "", title="Table", description="Nom de la table ; vide = le nom du fichier.", examples=["communes"]
        )
        mode: Literal["create", "overwrite", "append"] = mode(layer=True)


# ------------------------------------------------------------------------------------------------ anciens nœuds

READERS: dict[str, type[FileReader]] = {
    fmt: cls
    for cls in (CsvReader, ExcelReader, GeoPackageReader, ShapefileReader, GeoJsonReader, FlatGeobufReader,
                GeoParquetReader, MapInfoReader, SpatiaLiteReader)
    for fmt in cls.formats
}  # fmt: skip
WRITERS: dict[str, type[FileWriter]] = {
    fmt: cls
    for cls in (CsvWriter, ExcelWriter, GeoPackageWriter, ShapefileWriter, GeoJsonWriter, FlatGeobufWriter,
                GeoParquetWriter, MapInfoWriter, SpatiaLiteWriter)
    for fmt in cls.formats
}  # fmt: skip


def convert_legacy_node(node: dict[str, Any]) -> dict[str, Any]:
    """Un nœud « Reader » / « Writer » (tous formats) devient le lecteur / l'écrivain de son format, avec ses seuls
    réglages. Inchangé si le format ne se déduit pas (chemin sans extension, variable…)."""
    kinds = {"Reader": READERS, "Writer": WRITERS}.get(node.get("type", ""))
    if kinds is None:
        return node
    params = dict(node.get("params") or {})
    fmt = params.get("format") or "auto"
    if fmt == "auto":
        try:
            fmt = formats.detect(str(params.get("path") or ""))
        except ConfigError:
            return node
    target = kinds.get(fmt)
    if target is None:
        return node
    if "layer" in params and "sheet" in target.Params.model_fields:
        params["sheet"] = params.pop("layer")
    fields = target.Params.model_fields
    kept = {k: v for k, v in params.items() if k in fields}
    return {**node, "type": target.name, "params": kept}
