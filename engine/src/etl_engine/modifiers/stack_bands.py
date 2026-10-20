"""StackBands : réunit des rasters (un par bande) en un seul raster multibande."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import numpy as np
import pyarrow as pa
from pydantic import Field

from etl_engine.blocking import collect
from etl_engine.raster import core, vrt
from etl_engine.sdk import AttributeList, ConfigError, Emit, Modifier, Params


class StackBands(Modifier):
    name = "StackBands"
    label = "Empiler des bandes"
    category = "Rasters"
    description = "Réunit plusieurs rasters (une bande B04, une bande B08…) en un seul raster multibande."
    help = (
        "Les rasters reçus (par groupe, avec « Regrouper par ») deviennent les bandes d'un seul raster, sur la grille du "
        "premier. Si tous partagent la même grille, le résultat est un raster virtuel (instantané, sans copie) ; sinon "
        "(résolutions ou emprises différentes) ils sont rééchantillonnés sur la grille du premier et écrits dans un fichier. "
        "« Bande retenue » : la première bande de chaque raster, ou toutes. Les noms des bandes viennent de "
        "« Attribut du nom » (par exemple le nom du fichier) ou, à défaut, de la liste « Noms » puis des noms d'origine."
    )
    rejected = False

    class Params(Params):
        group_by: AttributeList = Field(
            default_factory=list,
            title="Regrouper par",
            description="Un raster multibande par valeur ; vide = un seul pour tous.",
        )
        which: Literal["first", "all"] = Field(
            "first",
            title="Bande retenue",
            description="first : la première bande de chaque raster ; all : toutes ses bandes.",
        )
        name_attribute: str = Field("", title="Attribut du nom", description="Attribut dont la valeur nomme la bande.")
        names: list[str] = Field(
            default_factory=list, title="Noms", description="Noms des bandes dans l'ordre (sans attribut du nom)."
        )
        resampling: Literal["nearest", "bilinear", "cubic", "average"] = Field(
            "bilinear", title="Rééchantillonnage", description="Si les grilles diffèrent."
        )

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        schema = inputs.get("input")
        core.check_raster_schema(schema)
        if schema is not None:
            for a in [*self.params.group_by, self.params.name_attribute]:
                if a and a not in schema.names:
                    raise ConfigError(f"attribut absent : {a}")
        keep = [schema.field(a) for a in self.params.group_by if schema is not None and a in schema.names]
        return {"output": core.raster_schema(keep)}

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        p = self.params
        table = collect(inputs.get("input", ()))
        if table is None or table.num_rows == 0:
            return
        descs = core.descriptors(table)
        attrs = core.attributes_of(table)
        for a in [*p.group_by, p.name_attribute]:
            if a and a not in attrs:
                raise ConfigError(f"attribut absent : {a}")
        groups: dict[tuple, list[int]] = {}
        for i in range(table.num_rows):
            groups.setdefault(tuple(attrs[a][i] for a in p.group_by), []).append(i)
        outputs, columns = [], {a: [] for a in p.group_by}
        for key, rows in groups.items():
            self.ctx.check_cancel()
            entries: list[tuple[dict[str, Any], int, str]] = []
            for i in rows:
                d = descs[i]
                bands = [1] if p.which == "first" else list(range(1, d["count"] + 1))
                for b in bands:
                    original = d["band_names"][b - 1] if b <= len(d["band_names"]) else f"b{b}"
                    if p.name_attribute:
                        base = str(attrs[p.name_attribute][i])
                        name = base if len(bands) == 1 else f"{base}_{original}"
                    else:
                        name = original
                    entries.append((d, b, name))
            if not p.name_attribute:
                for k, given in enumerate(p.names[: len(entries)]):
                    if given:
                        entries[k] = (entries[k][0], entries[k][1], given)
            first = entries[0][0]
            same = all(core.grid_of(d) == core.grid_of(first) for d, _, _ in entries)
            nodata = first.get("nodata")
            if same and len({d["dtype"] for d, _, _ in entries}) == 1:
                path = core.output_path(self.ctx, "stack", ".vrt")
                vrt.stack(entries, path)
                desc = core.describe(str(path), [n for _, _, n in entries])
            else:
                dtype = "float32" if len({d["dtype"] for d, _, _ in entries}) > 1 else first["dtype"]
                out_nodata = core.nodata_for(dtype, nodata)

                def compute(window, cache, entries=entries, first=first, dtype=dtype, out_nodata=out_nodata):
                    bands = []
                    for d, b, _ in entries:
                        ds = cache.aligned(d["uri"], core.grid_of(first), p.resampling)
                        data = ds.read(b, window=window, masked=True)
                        bands.append(np.ma.filled(data.astype(dtype), out_nodata if out_nodata is not None else 0))
                    return np.stack(bands)

                desc = core.run_blocks(
                    self.ctx,
                    "stack",
                    core.grid_of(first),
                    compute,
                    count=len(entries),
                    dtype=dtype,
                    nodata=out_nodata,
                    band_names=[n for _, _, n in entries],
                )
            outputs.append(desc)
            for a, v in zip(p.group_by, key, strict=True):
                columns[a].append(v)
        emit("output", core.to_table(outputs, columns))
        self.ctx.info(f"{len(outputs)} raster(s) multibande(s) à partir de {len(descs)}")
