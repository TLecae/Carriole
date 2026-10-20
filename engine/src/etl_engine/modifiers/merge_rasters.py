"""MergeRasters : assemble des rasters côte à côte (mosaïque) ou les superpose dans le temps (composite)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import numpy as np
import pyarrow as pa
from pydantic import Field

from etl_engine.blocking import collect
from etl_engine.raster import core, vrt
from etl_engine.sdk import AttributeList, ConfigError, Emit, Modifier, Params

METHODS = {
    "first": None,
    "last": None,
    "median": np.nanmedian,
    "mean": np.nanmean,
    "max": np.nanmax,
    "min": np.nanmin,
}


class MergeRasters(Modifier):
    name = "MergeRasters"
    label = "Mosaïque et composite de rasters"
    category = "Rasters"
    description = "Assemble des rasters côte à côte (dalles d'un MNT), ou en fait un composite temporel (médiane de plusieurs dates)."
    help = (
        "« spatial » : les rasters (même système de coordonnées, même taille de pixel, même nombre de bandes) sont posés "
        "sur leur emprise commune ; là où ils se recouvrent, le dernier qui a une donnée l'emporte (ordre de « Trier "
        "par »). Le résultat est un raster virtuel (VRT, instantané, sans copie) ou, avec « Écrire en GeoTIFF », un vrai "
        "fichier. « composite » : les rasters doivent avoir exactement la même grille ; chaque pixel prend la valeur "
        "first / last / median / mean / max / min de ses valeurs aux différentes dates (les pixels sans donnée sont "
        "ignorés) : un composite sans nuages, par exemple. « Regrouper par » fait un résultat par valeur d'attribut (une "
        "mosaïque par tuile, un composite par mois)."
    )
    rejected = False

    class Params(Params):
        mode: Literal["spatial", "composite"] = Field(
            "spatial",
            title="Mode",
            description="spatial : mosaïque d'emprises ; composite : même emprise, dates différentes.",
        )
        group_by: AttributeList = Field(
            default_factory=list, title="Regrouper par", description="Un résultat par valeur ; vide = un seul résultat."
        )
        sort_by: str = Field(
            "", title="Trier par", description="Attribut qui ordonne les rasters (le dernier recouvre les autres)."
        )
        method: Literal["first", "last", "median", "mean", "max", "min"] = Field(
            "median", title="Méthode (composite)", description="Valeur retenue pour chaque pixel."
        )
        write: bool = Field(
            False,
            title="Écrire en GeoTIFF",
            description="Spatial : écrit un vrai fichier au lieu d'un raster virtuel (toujours vrai pour un composite).",
        )

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        core.check_raster_schema(inputs.get("input"))
        schema = inputs.get("input")
        if schema is not None:
            for a in [*self.params.group_by, self.params.sort_by]:
                if a and a not in schema.names:
                    raise ConfigError(f"attribut absent : {a}")
        keep = [schema.field(a) for a in self.params.group_by if schema is not None and a in schema.names]
        return {"output": core.raster_schema(keep, crs=None if schema is None else None)}

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        p = self.params
        table = collect(inputs.get("input", ()))
        if table is None or table.num_rows == 0:
            return
        descs = core.descriptors(table)
        attrs = core.attributes_of(table)
        for a in [*p.group_by, p.sort_by]:
            if a and a not in attrs:
                raise ConfigError(f"attribut absent : {a}")
        groups: dict[tuple, list[int]] = {}
        for i in range(table.num_rows):
            groups.setdefault(tuple(attrs[a][i] for a in p.group_by), []).append(i)
        outputs, columns = [], {a: [] for a in p.group_by}
        for key, rows in groups.items():
            self.ctx.check_cancel()
            if p.sort_by:
                rows = sorted(rows, key=lambda i: (attrs[p.sort_by][i] is None, attrs[p.sort_by][i]))
            members = [descs[i] for i in rows]
            stem = "mosaic" if p.mode == "spatial" else "composite"
            if p.mode == "spatial":
                path = core.output_path(self.ctx, stem, ".vrt")
                _, desc = vrt.mosaic(members, path)
                if p.write:
                    desc = self._materialize(desc, stem)
            else:
                desc = self._composite(members, stem)
            outputs.append(desc)
            for a, value in zip(p.group_by, key, strict=True):
                columns[a].append(value)
        emit("output", core.to_table(outputs, columns))
        self.ctx.info(f"{len(outputs)} raster(s) assemblé(s) à partir de {len(descs)}")

    def _materialize(self, desc: dict[str, Any], stem: str) -> dict[str, Any]:
        dtype, nodata = desc["dtype"], core.nodata_for(desc["dtype"], desc.get("nodata"))

        def compute(window, cache):
            data = cache.open(desc["uri"]).read(window=window, masked=True)
            return np.ma.filled(data, nodata if nodata is not None else 0).astype(dtype, copy=False)

        return core.run_blocks(
            self.ctx,
            stem,
            core.grid_of(desc),
            compute,
            count=desc["count"],
            dtype=dtype,
            nodata=nodata,
            band_names=desc["band_names"],
        )

    def _composite(self, members: list[dict[str, Any]], stem: str) -> dict[str, Any]:
        p = self.params
        first = members[0]
        for d in members[1:]:
            if core.grid_of(d) != core.grid_of(first) or d["count"] != first["count"]:
                raise ConfigError(
                    "un composite demande des rasters de même grille (système, pixels, emprise) : utiliser « Rééchantillonner / aligner »"
                )
        dtype, nodata = first["dtype"], core.nodata_for(first["dtype"], first.get("nodata"))
        reducer = METHODS[p.method]

        def compute(window, cache):
            stack = []
            for d in members:
                values, valid = core.read_window(cache.open(d["uri"]), window)
                stack.append(np.where(valid, values, np.nan))
            cube = np.stack(stack)  # dates, bandes, lignes, colonnes
            if reducer is None:
                order = range(len(members)) if p.method == "first" else range(len(members) - 1, -1, -1)
                result = np.full(cube.shape[1:], np.nan, dtype=cube.dtype)
                for k in order:
                    result = np.where(np.isnan(result), cube[k], result)
            else:
                with np.errstate(all="ignore"), _quiet():
                    result = reducer(cube, axis=0)
            filled = np.where(np.isnan(result), nodata if nodata is not None else 0, result)
            if np.issubdtype(np.dtype(dtype), np.integer):
                filled = np.rint(filled)
            return filled.astype(dtype)

        return core.run_blocks(
            self.ctx,
            stem,
            core.grid_of(first),
            compute,
            count=first["count"],
            dtype=dtype,
            nodata=nodata,
            band_names=first["band_names"],
        )


class _quiet:  # noqa: N801 - gestionnaire de contexte local
    """Les pixels sans donnée à toutes les dates donnent un RuntimeWarning de numpy : attendu, donc tu."""

    def __enter__(self) -> None:
        import warnings

        self._ctx = warnings.catch_warnings()
        self._ctx.__enter__()
        warnings.simplefilter("ignore", RuntimeWarning)

    def __exit__(self, *exc: Any) -> None:
        self._ctx.__exit__(*exc)
