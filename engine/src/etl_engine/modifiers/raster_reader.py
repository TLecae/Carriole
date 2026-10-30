"""RasterReader : référence des rasters (GeoTIFF, COG, ASC, JP2, VRT…) sans lire leurs pixels."""

from __future__ import annotations

import glob
import os
from collections.abc import Mapping
from typing import Any

import pyarrow as pa
from pydantic import Field

from etl_engine import paths
from etl_engine.raster import core, vrt
from etl_engine.sdk import ConfigError, Emit, FilePath, Modifier, Params


class RasterReader(Modifier):
    name = "RasterReader"
    label = "Lecture de rasters"
    category = "Rasters"
    description = "Référence un ou plusieurs fichiers raster (GeoTIFF, COG, ASC, JP2, VRT…) : une entité par raster."
    help = (
        "Les pixels ne sont pas copiés : chaque entité décrit un raster (fichier, taille, bandes, système de coordonnées) et "
        "porte son emprise comme géométrie, ce qui permet de les voir sur la carte, de les filtrer ou de les joindre. "
        "Le chemin accepte un fichier, un motif (D:/mnt/*.tif, **/*.asc), une liste (« Autres fichiers ») ou une adresse "
        "web https:// (COG distant, lu par plages sans tout télécharger). « Bandes » choisit des bandes (numéros à partir "
        "de 1, ou noms) ; « Noms des bandes » nomme celles du résultat (utilisés dans RasterCalculator). Les formats lus "
        "sont ceux de GDAL."
    )
    inputs = ()
    rejected = False

    class Params(Params):
        path: FilePath = Field(
            "",
            title="Fichier",
            description="Fichier, motif (*.tif, **/*.asc) ou adresse https://.",
            examples=["D:/rasters/mnt/*.asc"],
        )
        paths: list[FilePath] = Field(
            default_factory=list, title="Autres fichiers", description="Fichiers ou motifs lus en plus de « Fichier »."
        )
        bands: list[str] = Field(
            default_factory=list,
            title="Bandes",
            description="Numéros (à partir de 1) ou noms des bandes à garder ; vide = toutes.",
        )
        band_names: list[str] = Field(
            default_factory=list,
            title="Noms des bandes",
            description="Noms donnés aux bandes du résultat, dans l'ordre.",
        )
        add_filename: bool = Field(
            True, title="Ajouter le nom du fichier", description="Ajoute l'attribut « filename »."
        )

    def _patterns(self) -> list[str]:
        return [x for x in [self.params.path, *self.params.paths] if x]

    def _files(self) -> list[str]:
        files: list[str] = []
        for pattern in self._patterns():
            if core.is_remote(pattern):
                found = [pattern]
            else:
                found = sorted(f for f in glob.glob(pattern, recursive=True) if os.path.isfile(f))
                for f in found:
                    paths.check(f, "read")
            files.extend(found)
        return list(dict.fromkeys(files))

    def fingerprint_extra(self) -> str:
        parts = []
        for f in self._files():
            if not core.is_remote(f):
                st = os.stat(f)
                parts.append(f"{f}:{st.st_mtime_ns}:{st.st_size}")
        return "|".join(parts)

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        if not self._patterns():
            raise ConfigError("chemin du fichier non renseigné")
        extra = [pa.field("filename", pa.string())] if self.params.add_filename else []
        return {"output": core.raster_schema(extra)}

    def _selected(self, desc: dict[str, Any]) -> tuple[list[int], list[str]]:
        """Indices (à partir de 1) et noms des bandes gardées."""
        names = desc["band_names"]
        wanted = self.params.bands
        if not wanted:
            indexes = list(range(1, desc["count"] + 1))
        else:
            indexes = []
            for w in wanted:
                if w.strip().isdigit():
                    index = int(w)
                elif w in names:
                    index = names.index(w) + 1
                else:
                    raise ConfigError(f"bande introuvable : {w} (bandes : {', '.join(names)})")
                if not 1 <= index <= desc["count"]:
                    raise ConfigError(f"bande introuvable : {w} (le raster a {desc['count']} bande(s))")
                indexes.append(index)
        out_names = [names[i - 1] for i in indexes]
        for position, new in enumerate(self.params.band_names):
            if position < len(out_names) and new:
                out_names[position] = new
        return indexes, out_names

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        files = self._files()
        if not files:
            raise ConfigError(f"fichier introuvable : {self.params.path or ', '.join(self.params.paths)}")
        descs, names = [], []
        for uri in files:
            self.ctx.check_cancel()
            try:
                desc = core.describe(uri)
            except Exception as exc:  # noqa: BLE001 - rasterio lève des erreurs de types variés selon le format
                raise ConfigError(f"raster illisible : {uri} ({exc})") from exc
            indexes, out_names = self._selected(desc)
            if indexes != list(range(1, desc["count"] + 1)) or out_names != desc["band_names"]:
                path = core.output_path(self.ctx, os.path.splitext(os.path.basename(uri))[0], ".vrt")
                vrt.subset(desc, indexes, out_names, path)
                desc = core.describe(str(path), out_names)
            descs.append(desc)
            names.append(os.path.basename(uri))
        table = core.to_table(descs, {"filename": names} if self.params.add_filename else None)
        self.ctx.info(f"{len(descs)} raster(s) référencé(s)")
        emit("output", table)
