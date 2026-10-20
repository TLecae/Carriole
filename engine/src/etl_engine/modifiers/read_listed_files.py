"""ReadListedFiles : lit le fichier désigné par chaque entité reçue (un dossier de fichiers, une liste)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Literal

import pyarrow as pa
from pydantic import Field

from etl_engine import formats, paths
from etl_engine.sdk import Attribute, AttributeList, ConfigError, Crs, Emit, FilePath, Modifier, Params

Format = Literal["auto", "parquet", "gpkg", "csv", "geojson", "fgb", "shp", "tab", "mif", "xlsx"]


def _merge(a: pa.Field, b: pa.Field) -> pa.Field:
    try:
        return pa.unify_schemas([pa.schema([a]), pa.schema([b])], promote_options="permissive").field(0)
    except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError):
        return pa.field(a.name, pa.string())  # types inconciliables : du texte


def unify(schemas: list[pa.Schema]) -> pa.Schema:
    """Union des schémas par nom ; deux types qui ne se concilient pas deviennent du texte."""
    fields: dict[str, pa.Field] = {}
    for schema in schemas:
        for field in schema:
            known = fields.get(field.name)
            if known is None or pa.types.is_null(known.type):
                fields[field.name] = field
            elif known.type != field.type and not pa.types.is_null(field.type):
                fields[field.name] = _merge(known, field)
    metadata = next((s.metadata for s in schemas if s.metadata), None)
    return pa.schema(list(fields.values()), metadata=metadata)


def conform(batch: pa.Table, schema: pa.Schema) -> pa.Table:
    """Remet un lot au schéma commun : colonnes manquantes vides, types alignés, ordre identique."""
    columns = []
    for field in schema:
        if field.name in batch.column_names:
            column = batch.column(field.name)
            columns.append(column if column.type == field.type else column.cast(field.type))
        else:
            columns.append(pa.nulls(batch.num_rows, field.type))
    return pa.table(columns, schema=schema)


class ReadListedFiles(Modifier):
    name = "ReadListedFiles"
    label = "Lire les fichiers listés"
    category = "Lots et variables"
    description = "Lit le fichier désigné par chaque entité reçue et envoie leurs entités."
    help = (
        "Chaque entité reçue donne un chemin de fichier (attribut « Chemin ») ; le fichier est lu avec les mêmes "
        "réglages que « Lecture de fichier » et ses entités sont envoyées. Les schémas des fichiers sont réunis par "
        "nom d'attribut : un attribut absent d'un fichier est vide, deux types incompatibles deviennent du texte. Pour "
        "que le schéma soit connu avant l'exécution (formulaires des nœuds suivants), indiquer un fichier d'exemple. "
        "« Attributs à recopier » ajoute à chaque entité lue des attributs de l'entité reçue (par exemple stem, le nom "
        "du fichier sans extension)."
    )
    inputs = ("input",)
    outputs = ("output",)
    rejected = False

    class Params(Params):
        path_attribute: Attribute = Field(
            "path", title="Chemin", description="Attribut qui contient le chemin du fichier à lire."
        )
        sample: FilePath = Field(
            "",
            title="Fichier d'exemple",
            description="Facultatif : donne le schéma avant l'exécution, pour les formulaires des nœuds suivants.",
        )
        copy_attributes: AttributeList = Field(
            default_factory=list,
            title="Attributs à recopier",
            description="Attributs de l'entité reçue ajoutés à chaque entité lue (un nom déjà présent est refusé).",
        )
        format: Format = Field("auto", title="Format", description="Déduit de l'extension si « auto ».")
        layer: str = Field("", title="Couche / feuille", description="Vide = la première.")
        cell_range: str = Field("", title="Plage (Excel)", description="Ex. A1:F200.")
        encoding: str = Field("", title="Encodage", description="Vide = détection automatique.")
        where: str = Field("", title="Filtre attributaire", description="Condition SQL appliquée à la lecture (GDAL).")
        delimiter: str = Field("", title="Séparateur (CSV)", description="Vide = détection automatique.")
        header: bool = Field(
            True, title="Première ligne = en-têtes (CSV, Excel)", description="Décocher si pas de ligne d'en-têtes."
        )
        detect_types: bool = Field(False, title="Détecter les types (CSV)", description="Sinon tout est lu en texte.")
        add_filename: bool = Field(
            False, title="Ajouter le nom du fichier", description="Ajoute la colonne « filename »."
        )
        wkt_column: str = Field("", title="Colonne WKT (CSV)", description="Colonne qui contient la géométrie en WKT.")
        x_column: str = Field("", title="Colonne X (CSV)", description="À renseigner avec la colonne Y.")
        y_column: str = Field("", title="Colonne Y (CSV)", description="À renseigner avec la colonne X.")
        crs: Crs = Field("", title="Système de coordonnées (CSV)", description="Celui de la colonne WKT ou de X/Y.")
        on_error: Literal["error", "skip"] = Field(
            "error",
            title="Fichier illisible",
            description="error : arrête le flux ; skip : ignore le fichier avec un avertissement.",
        )

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

    def _fmt(self, path: str) -> str:
        return self.params.format if self.params.format != "auto" else formats.detect(path)

    def _schema_of(self, path: str) -> pa.Schema:
        schema = formats.read_schema(self._fmt(path), path, **self._options())
        if self.params.add_filename and "filename" not in schema.names:
            schema = schema.append(pa.field("filename", pa.string()))
        return schema

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        p = self.params
        if not p.path_attribute:
            raise ConfigError("attribut du chemin non renseigné")
        upstream = inputs.get("input")
        if upstream is not None and p.path_attribute not in upstream.names:
            raise ConfigError(f"attribut absent : {p.path_attribute}")
        if not p.sample:
            return {"output": None}
        try:
            schema = self._schema_of(p.sample)
        except Exception:  # noqa: BLE001 - schéma inconnu : l'éditeur propose simplement moins d'attributs
            return {"output": None}
        extra = [upstream.field(a) for a in p.copy_attributes if upstream is not None and a in upstream.names]
        return {
            "output": pa.schema([*schema, *[f for f in extra if f.name not in schema.names]], metadata=schema.metadata)
        }

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        p = self.params
        rows: list[tuple[str, dict[str, Any]]] = []  # (chemin, attributs à recopier)
        types: dict[str, pa.DataType] = {}
        for batch in inputs.get("input", ()):
            self.ctx.check_cancel()
            if p.path_attribute not in batch.column_names:
                raise ConfigError(f"attribut absent : {p.path_attribute}")
            missing = [a for a in p.copy_attributes if a not in batch.column_names]
            if missing:
                raise ConfigError(f"attributs absents : {', '.join(missing)}")
            for name in p.copy_attributes:
                types.setdefault(name, batch.schema.field(name).type)
            copied = batch.select(p.copy_attributes).to_pylist() if p.copy_attributes else [{}] * batch.num_rows
            for value, attrs in zip(batch.column(p.path_attribute).to_pylist(), copied, strict=True):
                if value:
                    rows.append((str(value), attrs))
        if not rows:
            self.ctx.warning("aucun fichier à lire")
            return

        # première passe : les schémas de tous les fichiers (en-têtes seulement), réunis par nom
        usable: list[tuple[str, dict[str, Any]]] = []
        schemas: dict[str, pa.Schema | None] = {}
        for path, attrs in rows:
            self.ctx.check_cancel()
            if path not in schemas:
                try:
                    paths.check(path, "read")
                    if not os.path.exists(path):
                        raise ConfigError(f"fichier introuvable : {path}")
                    schemas[path] = self._schema_of(path)
                except Exception as exc:  # noqa: BLE001
                    if p.on_error == "error":
                        raise
                    self.ctx.warning(f"fichier ignoré : {path} ({exc})")
                    schemas[path] = None
            if schemas[path] is not None:
                usable.append((path, attrs))
        if not usable:
            return
        union = unify([s for s in schemas.values() if s is not None])
        clash = [a for a in p.copy_attributes if a in union.names]
        if clash:
            raise ConfigError(f"attributs déjà présents dans les fichiers : {', '.join(clash)}")

        # deuxième passe : les données, fichier après fichier, au schéma commun
        total = 0
        opts = self._options()
        for index, (path, attrs) in enumerate(usable, 1):
            try:
                for batch in formats.read_batches(self._fmt(path), path, self.ctx.batch_rows, **opts):
                    self.ctx.check_cancel()
                    if p.add_filename and "filename" not in batch.column_names:
                        batch = batch.append_column("filename", pa.array([os.path.basename(path)] * batch.num_rows))
                    batch = conform(batch, union)
                    for name in p.copy_attributes:
                        batch = batch.append_column(name, pa.array([attrs.get(name)] * batch.num_rows, types[name]))
                    total += batch.num_rows
                    self.ctx.progress(total)
                    emit("output", batch)
            except Exception as exc:  # noqa: BLE001
                if p.on_error == "error":
                    raise
                self.ctx.warning(f"fichier ignoré : {path} ({exc})")
            if index % 50 == 0:
                self.ctx.info(f"{index}/{len(usable)} fichiers lus")
        self.ctx.info(f"{total} entités lues dans {len(usable)} fichier(s)")
