"""SplitToFiles : un fichier par groupe d'entités, le chemin se compose avec les attributs (`{attribut}`)."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any, Literal

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
from pydantic import Field

from etl_engine.modifiers.writer import Format, abort_target, finish_target, open_target
from etl_engine.sdk import AttributeList, ConfigError, Emit, Modifier, OutPath, Params

_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
MAX_OPEN_FILES = 200

SUMMARY = pa.schema([("path", pa.string()), ("rows", pa.int64()), ("format", pa.string())])


def safe_name(value: Any) -> str:
    """Une valeur d'attribut devient un morceau de nom de fichier sûr."""
    text = "_vide" if value is None else str(value).strip()
    return _UNSAFE.sub("_", text).strip(". ") or "_vide"


def groups(batch: pa.Table, names: list[str]) -> Iterator[tuple[tuple[Any, ...], pa.Table]]:
    """Découpe un lot par valeurs des attributs `names` (sans boucle Python sur les lignes)."""
    if not names:
        yield (), batch
        return
    codes = np.zeros(batch.num_rows, dtype=np.int64)
    for name in names:
        column = batch.column(name).combine_chunks()
        encoded = pc.dictionary_encode(column)
        size = len(encoded.dictionary) + 1  # un code de plus pour « vide »
        index = encoded.indices.fill_null(size - 1).to_numpy(zero_copy_only=False).astype(np.int64)
        _, codes = np.unique(codes * size + index, return_inverse=True)
    order = np.argsort(codes, kind="stable")
    sorted_codes = codes[order]
    starts = np.concatenate(([0], np.flatnonzero(np.diff(sorted_codes)) + 1, [len(order)]))
    keys = batch.select(names)
    for begin, end in zip(starts[:-1], starts[1:], strict=True):
        rows = order[begin:end]
        key = tuple(keys.take(pa.array([rows[0]])).to_pylist()[0].values())
        yield key, batch.take(pa.array(rows))


class SplitToFiles(Modifier):
    name = "SplitToFiles"
    reusable = False  # toujours relancé (voir Modifier.reusable)
    label = "Écrire un fichier par groupe"
    category = "Lots et variables"
    description = "Écrit les entités dans plusieurs fichiers : le chemin se compose avec les valeurs d'attributs."
    help = (
        "Écrire {attribut} dans le chemin crée un fichier par valeur de cet attribut, ex. "
        "D:\\sorties\\{departement}\\communes.gpkg ou {departement}_{annee}.csv. Les caractères interdits dans un "
        "nom de fichier sont remplacés par _ et une valeur vide donne « _vide ». Les réglages du format sont ceux de "
        "« Écriture de fichier ». Chaque fichier est ouvert une seule fois : au plus 200 fichiers à la fois. La sortie "
        "« summary » donne une entité par fichier écrit (chemin, nombre d'entités, format), pour la suite du flux "
        "(journal, SetVariables, RunFlow…)."
    )
    inputs = ("input",)
    outputs = ("summary",)
    rejected = False
    stateful = True

    class Params(Params):
        path: OutPath = Field(
            "",
            title="Chemin du fichier",
            description="Chemin avec des {attributs} entre accolades ; dossiers créés si besoin.",
            examples=["D:/sorties/{departement}/communes.gpkg"],
        )
        format: Format = Field("auto", title="Format", description="Déduit de l'extension si « auto ».")
        layer: str = Field("", title="Couche / feuille", description="GeoPackage, MapInfo… : couche ; Excel : feuille.")
        mode: Literal["create", "overwrite", "append"] = Field(
            "overwrite",
            title="Mode",
            description="create : échoue si un fichier existe ; overwrite : le remplace ; append : ajoute.",
        )
        encoding: str = Field("", title="Encodage", description="Vide = celui du format (utf-8).")
        delimiter: str = Field(",", title="Séparateur (CSV)", description="Séparateur de colonnes du CSV écrit.")
        compression: Literal["zstd", "snappy", "gzip", "none"] = Field(
            "zstd", title="Compression (GeoParquet)", description="Compression des fichiers GeoParquet."
        )
        partition_by: AttributeList = Field(
            default_factory=list,
            title="Partitionner par (GeoParquet)",
            description="Colonnes dont chaque valeur donne un sous-dossier de chaque fichier.",
        )

    def _names(self) -> list[str]:
        if not self.params.path:
            raise ConfigError("chemin du fichier non renseigné")
        return list(dict.fromkeys(_PLACEHOLDER.findall(self.params.path)))

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        names = self._names()
        upstream = inputs.get("input")
        if upstream is not None:
            missing = [n for n in names if n not in upstream.names]
            if missing:
                raise ConfigError(f"attributs absents : {', '.join(missing)}")
        return {"summary": SUMMARY}

    def used_attributes(self) -> list[str]:
        return [*self._names(), *self.params.partition_by]

    def _path_for(self, values: Mapping[str, Any]) -> str:
        return _PLACEHOLDER.sub(lambda m: safe_name(values[m.group(1)]), self.params.path)

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        names = self._names()
        open_files: dict[str, dict[str, Any]] = {}
        try:
            for batch in inputs.get("input", ()):
                self.ctx.check_cancel()
                missing = [n for n in names if n not in batch.column_names]
                if missing:
                    raise ConfigError(f"attributs absents : {', '.join(missing)}")
                for key, part in groups(batch, names):
                    path = self._path_for(dict(zip(names, key, strict=True)))
                    target = open_files.get(path)
                    if target is None:
                        if len(open_files) >= MAX_OPEN_FILES:
                            raise ConfigError(
                                f"plus de {MAX_OPEN_FILES} fichiers à écrire en même temps : "
                                "regrouper sur un attribut moins fin"
                            )
                        writer, stage = open_target(self.params, path)
                        target = open_files[path] = {"writer": writer, "stage": stage, "rows": 0}
                    writer = target["writer"]
                    if hasattr(writer, "write_stream"):
                        writer.write_stream(iter([part]))  # un format qui écrit « en flux » (Excel) attend un itérateur
                    else:
                        writer.write(part)
                    target["rows"] += part.num_rows
        except BaseException:
            for target in open_files.values():
                abort_target(target["writer"], target["stage"])
            raise
        paths_written = []
        fmt = self.params.format
        try:
            for path, target in open_files.items():
                finish_target(target["writer"], target["stage"], self.ctx)
                paths_written.append(path)
        finally:
            for target in open_files.values():
                if target["stage"]:
                    target["stage"].discard()
        if not open_files:
            self.ctx.warning("aucune entité reçue : rien n'a été écrit")
            return
        from etl_engine import formats

        emit(
            "summary",
            pa.table(
                {
                    "path": paths_written,
                    "rows": pa.array([open_files[p]["rows"] for p in paths_written], pa.int64()),
                    "format": [fmt if fmt != "auto" else formats.detect(p) for p in paths_written],
                },
                schema=SUMMARY,
            ),
        )
        self.ctx.info(
            f"{sum(t['rows'] for t in open_files.values())} entités écrites dans {len(open_files)} fichier(s)"
        )
