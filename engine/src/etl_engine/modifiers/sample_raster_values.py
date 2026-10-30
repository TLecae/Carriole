"""SampleRasterValues : lit la valeur d'un raster sous chaque point."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pyarrow as pa
import rasterio
import shapely
from pydantic import Field
from rasterio.windows import Window

from etl_engine.blocking import collect
from etl_engine.data import GEOM, geometries, get_crs, to_crs, with_crs
from etl_engine.raster import core
from etl_engine.sdk import GEOMETRY_SLICE, ConfigError, Emit, Modifier, Params, slices

MAX_WINDOW_PIXELS = 16_000_000


class SampleRasterValues(Modifier):
    name = "SampleRasterValues"
    label = "Valeur du raster sous des points"
    category = "Rasters"
    description = "Ajoute à chaque point la valeur d'un raster à cet endroit (altitude d'un point, occupation du sol d'une parcelle)."
    help = (
        "Chaque entité de « points » reçoit, pour chaque bande choisie, la valeur du pixel qui la contient (attribut "
        "`préfixe` + nom de la bande). Les points sont reprojetés dans le système du raster. Avec plusieurs rasters, "
        "le premier qui recouvre le point est utilisé, et `_raster` donne son numéro (de 0). Les points hors de tous les "
        "rasters, ou sur un pixel sans donnée, ont une valeur vide ; les premiers sortent sur « Outside ». Les "
        "entités qui ne sont pas des points sont lues par leur point représentatif (centre de gravité intérieur)."
    )
    inputs = ("points", "raster")
    outputs = ("output", "Outside")
    rejected = False

    class Params(Params):
        bands: list[str] = Field(
            default_factory=list, title="Bandes", description="Numéros (de 1) ou noms des bandes ; vide = toutes."
        )
        prefix: str = Field("", title="Préfixe", description="Ajouté devant le nom de chaque bande dans les attributs.")

    def _selected(self, desc: dict[str, Any]) -> list[tuple[int, str]]:
        names = desc["band_names"]
        wanted = self.params.bands or [str(i) for i in range(1, desc["count"] + 1)]
        out = []
        for w in wanted:
            if w.strip().isdigit():
                index = int(w)
            elif w in names:
                index = names.index(w) + 1
            else:
                raise ConfigError(f"bande introuvable : {w}")
            if not 1 <= index <= desc["count"]:
                raise ConfigError(f"bande introuvable : {w} (le raster a {desc['count']} bande(s))")
            out.append((index, names[index - 1] if index <= len(names) and names[index - 1] else f"b{index}"))
        return out

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        points = inputs.get("points")
        core.check_raster_schema(inputs.get("raster"))
        if points is not None and GEOM not in points.names:
            raise ConfigError("les entités « points » n'ont pas de géométrie")
        return {"output": None, "Outside": points}

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        p = self.params
        rasters = collect(inputs.get("raster", ()))
        if rasters is None or rasters.num_rows == 0:
            raise ConfigError("aucun raster reçu")
        descs = core.descriptors(rasters)
        bands = self._selected(descs[0])
        names = [f"{p.prefix}{b}" for _, b in bands]
        for batch in slices(inputs.get("points", ()), GEOMETRY_SLICE):
            self.ctx.check_cancel()
            if GEOM not in batch.column_names:
                raise ConfigError("les entités « points » n'ont pas de géométrie")
            geoms = geometries(batch)
            ok = ~shapely.is_missing(geoms) & ~shapely.is_empty(geoms)
            reps = np.full(len(geoms), None, dtype=object)
            reps[ok] = shapely.point_on_surface(geoms[ok])
            values = np.full((len(bands), len(geoms)), np.nan)
            used = np.full(len(geoms), -1, dtype=np.int64)
            todo = ok.copy()
            for r, desc in enumerate(descs):
                if not todo.any():
                    break
                local = to_crs(reps[todo], get_crs(batch), desc["crs"])
                xs, ys = shapely.get_x(local), shapely.get_y(local)
                with core.gdal_env(), rasterio.open(core.gdal_uri(desc["uri"])) as ds:
                    rows, cols = rasterio.transform.rowcol(ds.transform, xs, ys, op=np.floor)
                    rows, cols = np.asarray(rows).astype(np.int64), np.asarray(cols).astype(np.int64)
                    inside = (rows >= 0) & (rows < ds.height) & (cols >= 0) & (cols < ds.width)
                    if not inside.any():
                        continue
                    r0, r1, c0, c1 = (
                        rows[inside].min(),
                        rows[inside].max() + 1,
                        cols[inside].min(),
                        cols[inside].max() + 1,
                    )
                    window = Window(int(c0), int(r0), int(c1 - c0), int(r1 - r0))
                    positions = np.flatnonzero(todo)[inside]
                    if window.width * window.height <= MAX_WINDOW_PIXELS:
                        data = ds.read([i for i, _ in bands], window=window, masked=True)
                        for k in range(len(bands)):
                            got = data[k][rows[inside] - r0, cols[inside] - c0]
                            values[k, positions] = np.ma.filled(got.astype("float64"), np.nan)
                    else:  # points très dispersés sur un grand raster : lecture point par point
                        sampled = np.array(
                            list(
                                ds.sample(zip(xs[inside], ys[inside], strict=True), [i for i, _ in bands], masked=True)
                            )
                        )
                        values[:, positions] = np.ma.filled(sampled.astype("float64"), np.nan).T
                    used[positions] = r
                    todo[positions] = False
            out = batch
            for k, name in enumerate(names):
                column = pa.array(values[k], pa.float64(), from_pandas=True)  # NaN : pas de valeur
                out = (
                    out.set_column(out.column_names.index(name), name, column)
                    if name in out.column_names
                    else out.append_column(name, column)
                )
            if len(descs) > 1:
                out = out.append_column("_raster", pa.array(used, pa.int64(), mask=used < 0))
            covered = used >= 0
            if covered.any():
                emit("output", with_crs(out.filter(pa.array(covered)), get_crs(batch)))
            if (~covered).any():
                emit("Outside", batch.filter(pa.array(~covered)))
