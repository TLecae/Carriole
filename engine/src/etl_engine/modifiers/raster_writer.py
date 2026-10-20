"""RasterWriter : écrit les rasters du flux dans des fichiers (GeoTIFF ou COG)."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import pyarrow as pa
from pydantic import Field
from rasterio.shutil import copy as raster_copy

from etl_engine import paths, staging
from etl_engine.modifiers.split_to_files import safe_name
from etl_engine.raster import core
from etl_engine.sdk import ConfigError, Emit, Modifier, OutPath, Params

_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


class RasterWriter(Modifier):
    name = "RasterWriter"
    reusable = False  # toujours relancé (voir Modifier.reusable)
    label = "Écriture de rasters"
    category = "Rasters"
    description = "Écrit chaque raster dans un fichier GeoTIFF ou COG (cloud optimized), avec vues d'ensemble."
    help = (
        "Le chemin est un fichier, ou un modèle avec des {attributs} (un fichier par raster : D:/sorties/{filename}), "
        "ou un dossier (le fichier prend le nom du raster d'origine). « cog » écrit un GeoTIFF optimisé pour le nuage "
        "(tuiles, vues d'ensemble, lisible par plages sur le web) ; « gtiff » un GeoTIFF tuilé ordinaire, avec "
        "vues d'ensemble si demandé. Compression sans perte (deflate ou zstd). Sur le partage de l'équipe, le fichier "
        "est écrit en local puis publié d'un bloc. Un raster déjà existant est refusé en mode « create »."
    )
    inputs = ("input",)
    outputs = ("summary",)
    rejected = False

    class Params(Params):
        path: OutPath = Field(
            "",
            title="Fichier ou dossier",
            description="Fichier .tif, modèle avec {attributs}, ou dossier.",
            examples=["D:/sorties/rasters/{filename}"],
        )
        format: Literal["cog", "gtiff"] = Field(
            "cog", title="Format", description="cog : cloud optimized GeoTIFF ; gtiff : GeoTIFF tuilé."
        )
        compression: Literal["deflate", "zstd", "lzw", "none"] = Field(
            "deflate", title="Compression", description="Compression sans perte."
        )
        overviews: bool = Field(
            True, title="Vues d'ensemble", description="Ajoute les vues d'ensemble (pyramide) : affichage rapide."
        )
        mode: Literal["create", "overwrite"] = Field(
            "overwrite", title="Mode", description="create : échoue si le fichier existe ; overwrite : le remplace."
        )

    def _names(self) -> list[str]:
        if not self.params.path:
            raise ConfigError("chemin du fichier non renseigné")
        return list(dict.fromkeys(_PLACEHOLDER.findall(self.params.path)))

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        self._names()
        core.check_raster_schema(inputs.get("input"))
        return {"summary": pa.schema([("path", pa.string()), ("format", pa.string()), ("size", pa.int64())])}

    def _target(self, desc: dict[str, Any], attrs: Mapping[str, Any], count: int) -> str:
        path = self.params.path
        names = self._names()
        if names:
            return _PLACEHOLDER.sub(lambda m: safe_name(attrs.get(m.group(1))), path)
        stem = Path(desc["uri"].split("?")[0]).stem
        if path.endswith(("/", "\\")) or not Path(path).suffix:
            return str(Path(path) / f"{stem}.tif")
        if count > 1:
            raise ConfigError("plusieurs rasters : écrire un dossier ou un modèle avec {attributs} dans le chemin")
        return path

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        p = self.params
        self._names()
        written: list[tuple[str, str, int]] = []
        for batch in inputs.get("input", ()):
            self.ctx.check_cancel()
            descs = core.descriptors(batch)
            attributes = core.attributes_of(batch)
            for i, desc in enumerate(descs):
                attrs = {k: v[i] for k, v in attributes.items()}
                target = self._target(desc, attrs, len(descs))
                if not target.lower().endswith((".tif", ".tiff")):
                    target += ".tif"
                paths.check(target, "write")
                if os.path.exists(target) and p.mode == "create":
                    raise ConfigError(f"le fichier existe déjà : {target}")
                Path(target).parent.mkdir(parents=True, exist_ok=True)
                stage = staging.begin(target, "tif", p.mode)
                work = str(stage.work_path) if stage else target
                options: dict[str, Any] = {"compress": p.compression.upper() if p.compression != "none" else "NONE"}
                with core.gdal_env():
                    if p.format == "cog":
                        raster_copy(
                            core.gdal_uri(desc["uri"]),
                            work,
                            driver="COG",
                            overview_resampling="average",
                            **options,
                            num_threads="ALL_CPUS",
                        )
                    else:
                        raster_copy(
                            core.gdal_uri(desc["uri"]),
                            work,
                            driver="GTiff",
                            tiled=True,
                            blockxsize=512,
                            blockysize=512,
                            bigtiff="IF_SAFER",
                            **options,
                        )
                        if p.overviews:
                            import rasterio
                            from rasterio.enums import Resampling

                            with rasterio.open(work, "r+") as ds:
                                factors = [2**k for k in range(1, 8) if max(ds.width, ds.height) / 2**k >= 256]
                                if factors:
                                    ds.build_overviews(factors, Resampling.average)
                if stage:
                    try:
                        stage.publish()
                    finally:
                        stage.discard()
                written.append((target, p.format, os.path.getsize(target)))
                self.ctx.info(f"{target} écrit")
        if written:
            emit(
                "summary",
                pa.table(
                    {
                        "path": [w[0] for w in written],
                        "format": [w[1] for w in written],
                        "size": pa.array([w[2] for w in written], pa.int64()),
                    }
                ),
            )
        else:
            self.ctx.warning("aucun raster reçu : rien n'a été écrit")
