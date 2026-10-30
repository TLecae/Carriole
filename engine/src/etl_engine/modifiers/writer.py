"""Écriture dans un fichier ((Geo)Parquet, GeoPackage, CSV, …)."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from etl_engine import formats, paths, staging
from etl_engine.sdk import AttributeList, ConfigError, Emit, Modifier, OutPath, Params

Format = Literal["auto", "parquet", "gpkg", "csv", "geojson", "fgb", "shp", "tab", "mif", "xlsx"]


class Writer(Modifier):
    name = "Writer"
    label = "Écriture de fichier"
    category = "Écriture"
    hidden = True  # remplacé par un écrivain par format (file_io) ; les flux sont convertis à l'ouverture
    description = "Écrit les entités reçues dans un fichier."
    help = (
        "Modes : « create » échoue si le fichier existe, « overwrite » le remplace, « append » ajoute. "
        "Pour un GeoPackage (couches) ou un classeur Excel (feuilles), plusieurs Writer peuvent écrire des "
        "éléments différents dans le même fichier : en mode « overwrite » seule la couche ou la feuille visée est "
        "remplacée. Shapefile : noms de colonnes limités à 10 caractères (les renommages sont journalisés), "
        "fichier .cpg en UTF-8, limite de 2 Go. (Geo)Parquet : « partitionner par » écrit un dossier par valeur. "
        "Sur le partage de l'équipe, le fichier est écrit en local puis publié d'un bloc : une erreur ou un arrêt en "
        "cours de route laisse le fichier existant intact, et l'erreur de publication est signalée (fichier ouvert "
        "par un collègue, partage indisponible)."
    )
    inputs = ("input",)
    outputs = ()
    rejected = False
    stateful = True

    class Params(Params):
        path: OutPath = Field(
            "",
            title="Fichier",
            description="Chemin du fichier à écrire (dossiers créés si besoin).",
            examples=["D:/sorties/resultat.gpkg"],
        )
        format: Format = Field("auto", title="Format", description="Déduit de l'extension si « auto ».")
        layer: str = Field(
            "",
            title="Couche / feuille",
            description="GeoPackage, MapInfo… : nom de la couche écrite ; Excel : nom de la feuille.",
            examples=["communes"],
        )
        mode: Literal["create", "overwrite", "append"] = Field(
            "overwrite",
            title="Mode",
            description="create : échoue si le fichier existe ; overwrite : le remplace ; append : ajoute.",
        )
        encoding: str = Field("", title="Encodage", description="Vide = celui du format (utf-8).")
        delimiter: str = Field(",", title="Séparateur (CSV)", description="Séparateur de colonnes du CSV écrit.")
        compression: Literal["zstd", "snappy", "gzip", "none"] = Field(
            "zstd", title="Compression ((Geo)Parquet)", description="Compression des fichiers (Geo)Parquet."
        )
        partition_by: AttributeList = Field(
            default_factory=list,
            title="Partitionner par ((Geo)Parquet)",
            description="Colonnes dont chaque valeur donne un sous-dossier ; « Fichier » est alors un dossier.",
        )

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        p = self.params
        if not p.path:
            raise ConfigError("chemin du fichier non renseigné")
        writer, stage = open_target(p, p.path)
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


def open_target(p: Any, path: str, fmt: str | None = None) -> tuple[Any, staging.StagedFile | None]:
    """Ouvre l'écriture de `path` selon les réglages `p` (mode, couche, encodage… : ceux que le format possède) :
    partagé par les écrivains de chaque format, Writer et SplitToFiles. `fmt` : sinon `p.format`, sinon l'extension.
    Sur un partage, l'écriture est locale puis publiée d'un bloc (voir `etl_engine.staging`)."""
    if fmt is None:
        chosen = getattr(p, "format", "auto")
        fmt = chosen if chosen != "auto" else formats.detect(path)
    paths.check(path, "write")  # dossiers autorisés (ETL_WRITE_ROOTS)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    partition_by = list(getattr(p, "partition_by", None) or [])
    encoding = getattr(p, "encoding", "")
    opts: dict[str, Any] = {"mode": p.mode}
    if fmt == "csv":
        opts["delimiter"] = getattr(p, "delimiter", ",") or ","
    elif fmt == "parquet":
        opts["compression"] = getattr(p, "compression", "zstd")
        if partition_by:
            opts["partition_by"] = partition_by
    else:
        opts["layer"] = getattr(p, "layer", None) or getattr(p, "sheet", None) or None
    if fmt not in ("parquet", "csv", "xlsx") and encoding:
        opts["encoding"] = encoding
    # Un dossier partitionné s'écrit directement.
    stage = None if partition_by else staging.begin(path, fmt, p.mode)
    try:
        return formats.open_writer(fmt, stage.work_path if stage else path, **opts), stage
    except BaseException:
        if stage:
            stage.discard()
        raise


def abort_target(writer: Any, stage: staging.StagedFile | None) -> None:
    """Écriture interrompue : la destination n'a pas été touchée."""
    try:
        getattr(writer, "abort", writer.close)()
    finally:
        if stage:
            stage.discard()


def finish_target(writer: Any, stage: staging.StagedFile | None, ctx: Any) -> None:
    try:
        writer.close()
        for message in getattr(writer, "messages", ()):
            ctx.info(message)
        if stage:
            published = stage.publish()
            ctx.info(f"publié sur le partage : {', '.join(published) or 'rien'}")
    finally:
        if stage:
            stage.discard()
