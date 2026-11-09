"""Un lecteur et un écrivain par format de fichier : chacun n'a que les réglages de son format.

`FileReader` et `FileWriter` portent la mécanique commune (motifs de fichiers, schéma, lots, écriture locale puis
publiée sur un partage) ; chaque format déclare ses extensions et ses paramètres. Les anciens « Lecture de fichier »
et « Écriture de fichier », qui mélangeaient les réglages de tous les formats, restent lisibles : `flow.py` les
convertit à l'ouverture d'un flux (`convert_legacy_node`).
"""

from __future__ import annotations

import glob
import os
from collections.abc import Callable, Iterator, Mapping
from typing import Any, ClassVar, Literal

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
from pydantic import Field

from etl_engine import duck, formats, paths
from etl_engine.expr import (
    check_single_expression,
    expression_type,
    needs_spatial,
    quote,
    safe_mask,
    sql_base,
    sql_columns,
)
from etl_engine.modifiers.writer import abort_target, finish_target, open_target
from etl_engine.sdk import (
    AttributeList,
    ConfigError,
    Crs,
    Emit,
    FilePath,
    Modifier,
    Params,
    first_rows,
    missing_attributes,
    row_limit,
)

# réglages que les modules de formats savent lire (nom du paramètre -> option de lecture)
READ_OPTIONS = (
    "layer", "cell_range", "encoding", "where", "delimiter", "header", "detect_types", "add_filename",
    "wkt_column", "x_column", "y_column", "crs", "sql",
)  # fmt: skip


def path_field(
    extensions: tuple[str, ...], example: str, access: Literal["read", "write"] = "read", web: bool = False
) -> Any:
    """Champ « Fichier » : l'explorateur ne montre que les fichiers de ces extensions ; `web` : une adresse https://
    est aussi acceptée (lecteur `web`)."""
    extra: dict[str, Any] = {"x-etl-extensions": list(extensions)}
    if access == "write":
        extra["x-etl-access"] = "write"
    if access == "read" and web:
        description = (
            "Chemin du fichier, motif (*.ext, dossier/**/*.ext : tous les fichiers trouvés) ou adresse web https://."
        )
    elif access == "read":
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


def duck_where() -> Any:
    """Filtre des lecteurs DuckDB (CSV, Parquet) : une condition sur les colonnes du fichier, évaluée à la lecture."""
    return Field(
        "",
        title="Filtre (WHERE)",
        description=(
            "Condition SQL (DuckDB) appliquée à la lecture : seules les entités qui la vérifient sont lues, et DuckDB "
            "ne lit du fichier que les parties utiles ; vide = toutes les entités."
        ),
        examples=['"dep" = \'31\' AND "pop" > 1000'],
        json_schema_extra={"x-etl-widget": "sql", "x-etl-dialect": "duckdb"},
    )


def duck_columns() -> Any:
    """Attributs gardés par les lecteurs DuckDB (CSV, Parquet) : le SELECT de leur requête de lecture."""
    return Field(
        default_factory=list,
        title="Attributs lus (SELECT)",
        description=(
            "Attributs gardés à la lecture ; vide = tous. DuckDB ne lit du fichier que ces colonnes et celles du "
            "filtre : une adresse web en télécharge moins."
        ),
        json_schema_extra={"x-etl-widget": "read-columns"},
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
    web: ClassVar[bool] = False  # accepte aussi une adresse http(s) (lue par plages, sans téléchargement complet)

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
            if formats.is_remote(pattern):
                if not self.web:
                    raise ConfigError(f"ce lecteur ne lit pas d'adresse web : {pattern}")
                found = [pattern]
            else:
                found = sorted(glob.glob(pattern, recursive=True))
                for f in found:
                    paths.check(f, "read")  # dossiers autorisés (ETL_READ_ROOTS)
            files.extend(found)
        return list(dict.fromkeys(files))

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
        def version(f: str) -> str:
            if formats.is_remote(f):
                return formats.remote_stamp(f)  # ETag ou date : un fichier web modifié est relu
            st = os.stat(f)
            return f"{st.st_mtime_ns}:{st.st_size}"

        return "|".join(f"{f}:{version(f)}" for f in self._files())

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        fmt = self._fmt()
        files = self._files()
        for pattern in self._patterns():
            if not formats.is_remote(pattern) and not glob.glob(pattern, recursive=True):
                raise ConfigError(f"fichier introuvable : {pattern}")
        opts = self._options()
        sources = self._patterns() if fmt == "csv" else files  # le CSV gère lui-même les motifs (union par nom)
        add_filename = getattr(self.params, "add_filename", False) and fmt != "csv"
        if fmt == "parquet" and self._limit():
            opts["limit"] = self._limit()  # au plus autant par fichier : une adresse web n'en télécharge pas plus
        self._failed, self._failure = 0, ""

        def batches() -> Iterator[pa.Table]:
            for src in sources:
                for batch in formats.read_batches(fmt, src, self.ctx.batch_rows, **opts):
                    if add_filename and "filename" not in batch.column_names:
                        name = os.path.basename(src.split("?")[0])
                        batch = batch.append_column("filename", pa.repeat(pa.scalar(name, pa.string()), batch.num_rows))
                    yield self._keep(batch)

        total = 0
        for batch in first_rows(batches(), self.params.max_rows):
            self.ctx.check_cancel()
            total += batch.num_rows
            self.ctx.progress(total)
            emit("output", batch)
        if self._failed:
            self.ctx.warning(
                f"{self._failed} entité(s) non lue(s) : le filtre n'a pas pu être évalué ({self._failure})"
            )
        self.ctx.info(f"{total} entités lues")

    def _limit(self) -> int | None:
        return self.params.max_rows or None

    def _keep(self, batch: pa.Table) -> pa.Table:
        """Entités gardées d'un lot lu (les lecteurs DuckDB y appliquent leur filtre)."""
        return batch


class DuckDbReader(FileReader):
    """Lecteur d'un format que DuckDB lit lui-même, sur tous les cœurs : le fichier du port est écrit par DuckDB (voie
    SQL, ADR 0042), sans passer les lignes par Python. `run` reste la référence, et le repli si DuckDB échoue."""

    sql_fallback = True

    def _where(self) -> str:
        """Filtre DuckDB appliqué à la lecture (paramètre `where`) ; vide : toutes les entités."""
        return (getattr(self.params, "where", "") or "").strip()

    def _columns(self) -> list[str]:
        """Attributs gardés (paramètre `columns`, le SELECT) ; vide : tous."""
        return [c for c in getattr(self.params, "columns", None) or [] if c]

    def file_schema(self) -> pa.Schema | None:
        """Schéma du fichier lu, avant le choix des attributs : ceux que la requête de lecture propose."""
        return FileReader.output_schema(self, {})["output"]

    def _selected(self, schema: pa.Schema) -> pa.Schema:
        """Schéma de sortie : les attributs choisis, dans cet ordre (métadonnées gardées : SCR, GeoParquet)."""
        wanted = self._columns()
        if not wanted:
            return schema
        missing = [c for c in wanted if c not in schema.names]
        if missing:
            raise missing_attributes(missing)
        return pa.schema([schema.field(c) for c in wanted], metadata=schema.metadata)

    def _options(self) -> dict[str, Any]:
        out = super()._options()
        out.pop("where", None)  # SQL de DuckDB, appliqué ici (le `where` des autres lecteurs est celui de GDAL)
        return out

    def _limit(self) -> int | None:
        """Limite de lignes poussée dans la lecture : sans filtre seulement (avec lui, elle compte les lignes gardées)."""
        return None if self._where() else (self.params.max_rows or None)

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        out = super().output_schema(inputs)
        cond, schema = self._where(), out["output"]
        if schema is None:
            return out
        if cond:  # erreur de configuration : le run s'arrête avant de lire les données
            check_single_expression(cond)
            if not pa.types.is_boolean(expression_type(schema, cond)):
                raise ConfigError(f"le filtre « {cond} » doit être une condition (vrai ou faux)")
        return {"output": self._selected(schema)}

    def _keep(self, batch: pa.Table) -> pa.Table:
        cond = self._where()
        if not cond:
            return self._chosen(batch)
        mask, errors = safe_mask(batch, cond)
        if errors:  # une entité sur laquelle la condition échoue n'est pas lue ; le journal le dit
            self._failed += len(errors)
            self._failure = self._failure or next(iter(errors.values()))
            keep = np.ones(batch.num_rows, dtype=bool)
            keep[list(errors)] = False
            mask = pc.and_(mask, pa.array(keep))
        return self._chosen(batch.filter(mask))

    def _chosen(self, batch: pa.Table) -> pa.Table:
        """Les attributs choisis d'un lot lu, après le filtre (qui peut se servir des autres)."""
        return batch.select(self._selected(batch.schema).names) if self._columns() else batch

    def read_query(self) -> tuple[str, Callable[[Any], None]] | None:
        """Requête DuckDB qui lit tout le fichier (sans filtre ni limite), et la préparation de sa connexion : l'aide
        à la saisie y cherche les valeurs d'un attribut. None : ce fichier ne se lit pas par DuckDB."""
        found = self._sources()
        if found is None:
            return None
        fmt, sources = found
        opts = self._options()
        query = formats.sql_query(fmt, sources, limit=None, **opts)
        if query is None:
            return None
        return query, lambda con: formats.sql_prepare(fmt, con, sources, None, **opts)

    def _sources(self) -> tuple[str, list[str]] | None:
        fmt = self._fmt()
        files = self._files()
        if not files or any(not formats.is_remote(p) and not glob.glob(p, recursive=True) for p in self._patterns()):
            return None  # fichier introuvable : `run` le signale
        return fmt, self._patterns() if fmt == "csv" else files

    def sql(self, relations: Mapping[str, str], schemas: Mapping[str, pa.Schema | None]) -> dict[str, str] | None:
        found = self._sources()
        if found is None or self.output_schema({})["output"] is None:  # schéma (et SCR) inconnu : voie Python
            return None
        fmt, sources = found
        query = formats.sql_query(fmt, sources, limit=self._limit(), **self._options())
        cond, schema = self._where(), self.file_schema()
        if query is None or schema is None or not (cond or self._columns()):
            return None if query is None else {"output": query}
        names = self._selected(schema).names
        if not cond:  # attributs choisis : DuckDB ne lit que leurs colonnes
            return {"output": f"SELECT {', '.join(quote(n) for n in names)} FROM ({query})"}
        # filtre dans la requête de lecture : DuckDB ne lit que les colonnes et les groupes de lignes utiles ; une
        # condition qui échoue sur une entité fait échouer la requête, et la lecture repasse par lots (`run`)
        rows = f"({sql_base(f'({query})', schema, [cond])})"
        query = f"SELECT {sql_columns(names, schema, [cond])} FROM {rows} WHERE COALESCE(({cond}), FALSE)"
        return {"output": f"{query} LIMIT {int(self.params.max_rows)}" if self.params.max_rows else query}

    def ready_ports(self) -> dict[str, str] | None:
        found = self._sources()
        if found is None or self._where() or self._columns():  # filtré : le port n'est plus le fichier
            return None
        fmt, sources = found
        path = formats.as_port(fmt, sources, limit=self._limit(), **self._options())
        return None if path is None else {"output": path}

    def sql_prepare(self, con: Any) -> None:
        fmt, sources = self._sources() or (self._fmt(), [])
        formats.sql_prepare(fmt, con, sources, self._limit(), **self._options())
        if needs_spatial([self._where()]):
            duck.load_spatial(con)


class CsvReader(DuckDbReader):
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
        columns: list[str] = duck_columns()
        where: str = duck_where()
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


class GeoParquetReader(DuckDbReader):
    name = "GeoParquetReader"
    label = "Lecture (Geo)Parquet"
    description = (
        "Lit un fichier Parquet, avec ou sans géométrie (GeoParquet), sur le disque ou à une adresse web (https://)."
    )
    help = (
        "Un Parquet sans géométrie est lu tel quel. Pour un GeoParquet, la géométrie et son système de coordonnées sont "
        "lus dans ses métadonnées. Un fichier seul, local, sans nom du fichier ajouté ni limite de lignes, n'est pas "
        "recopié : le résultat du nœud est le fichier lui-même (le modifier ensuite oblige à relancer le flux). Une "
        "adresse https:// est lue par plages : seules les parties utiles du fichier sont téléchargées (proxy des "
        "Paramètres)."
    )
    formats = ("parquet",)
    web = True

    class Params(Params):
        path: str = path_field((".parquet", ".geoparquet"), "D:/donnees/communes.parquet", web=True)
        paths: list[FilePath] = other_files()
        add_filename: bool = filename_column()
        columns: list[str] = duck_columns()
        where: str = duck_where()
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

    def _where(self) -> tuple[str | None, str]:
        """Couche (ou feuille) et séparateur visés, comme `open_target`."""
        p = self.params
        return getattr(p, "layer", None) or getattr(p, "sheet", None) or None, getattr(p, "delimiter", ",") or ","

    def _appends(self) -> bool:
        """Ajout à un fichier que l'utilisateur peut écrire (dossiers autorisés) : sa couche se lit."""
        p = self.params
        if getattr(p, "mode", "") != "append" or not p.path:
            return False
        try:
            paths.check(p.path, "write")
        except (ConfigError, OSError, ValueError):
            return False
        return True

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        """Ajout à une couche existante : les attributs qu'elle ne peut pas recevoir sont une erreur de configuration,
        signalée dans l'éditeur, qui arrête le run avant toute lecture."""
        schema = inputs.get("input")
        if schema is not None and self._appends():
            formats.check_append(self._fmt(self.params.path), self.params.path, schema, *self._where())
        return {}

    def target(self, schema: pa.Schema | None) -> dict[str, Any]:
        """Pour l'éditeur, en mode ajout : colonnes de la couche visée (absente : créée à l'écriture) et attributs
        reçus, nommés comme ils y seraient écrits."""
        fmt = self._fmt(self.params.path)
        columns = formats.target_columns(fmt, self.params.path, *self._where()) if self._appends() else None
        return {
            "exists": columns is not None,
            "columns": columns or [],
            "case_sensitive": fmt in formats.CASE_SENSITIVE,
            "incoming": None if schema is None else formats.written_names(fmt, schema),
        }

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
    label = "Écriture (Geo)Parquet"
    description = (
        "Écrit les entités dans un fichier Parquet (GeoParquet s'il y a une géométrie) ou un dossier partitionné."
    )
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
