"""ClipRasterByMask : découpe des rasters par des polygones (ou par une emprise)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import numpy as np
import pyarrow as pa
import pyproj
import rasterio
import shapely
from pydantic import BaseModel, Field
from rasterio.features import geometry_mask
from rasterio.windows import Window

from etl_engine.blocking import collect
from etl_engine.data import GEOM, geometries, get_crs
from etl_engine.raster import core
from etl_engine.sdk import AttributeList, ConfigError, Emit, Modifier, Params


def to_crs(geoms: np.ndarray, source: str | None, target: str | None) -> np.ndarray:
    """Géométries reprojetées de `source` vers `target` (rien à faire si identiques ou inconnus)."""
    if not source or not target or source == target:
        return geoms
    transformer = pyproj.Transformer.from_crs(source, target, always_xy=True)
    return shapely.transform(geoms, lambda coords: np.column_stack(transformer.transform(coords[:, 0], coords[:, 1])))


class ClipRasterByMask(Modifier):
    name = "ClipRasterByMask"
    label = "Découpe de rasters"
    category = "Rasters"
    description = "Découpe chaque raster par des polygones (communes, parcelles) ou par une emprise ; les pixels hors du polygone sont masqués."
    help = (
        "Par polygones : la deuxième entrée (« mask ») donne les polygones, dans n'importe quel système (ils sont "
        "reprojetés). « union » : un seul raster par raster d'origine, découpé par la réunion des polygones ; « each » : un "
        "raster par polygone, avec ses attributs (« Attributs du masque à copier ») : par exemple un MNT par commune. "
        "« Recadrer » réduit le raster à l'emprise des polygones ; sinon il garde sa taille et seuls les pixels hors "
        "polygone sont masqués. « Pixels touchés » inclut tout pixel que le polygone touche, même un peu. Par emprise : "
        "Ouest, Sud, Est, Nord dans le système du raster. Un polygone qui ne recouvre pas le raster ne donne rien."
    )
    rejected = False

    class Params(Params):
        by: Literal["features", "extent"] = Field(
            "features",
            title="Découper par",
            description="features : polygones (entrée « mask ») ; extent : une emprise saisie.",
        )
        mode: Literal["union", "each"] = Field(
            "union", title="Polygones", description="union : un résultat par raster ; each : un résultat par polygone."
        )
        extent: list[float] = Field(
            default_factory=list,
            title="Emprise",
            description="Ouest, Sud, Est, Nord dans le système du raster (par emprise).",
            examples=[[600000, 6500000, 601000, 6501000]],
        )
        crop: bool = Field(True, title="Recadrer", description="Réduit le raster à l'emprise de la découpe.")
        all_touched: bool = Field(False, title="Pixels touchés", description="Garde tout pixel que le polygone touche.")
        mask_attributes: AttributeList = Field(
            default_factory=list,
            title="Attributs du masque à copier",
            description="Mode « each » : ajoutés aux rasters produits.",
        )

    @classmethod
    def ports(cls, params: BaseModel | None = None) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return (("input", "mask") if getattr(params, "by", "features") == "features" else ("input",)), cls.outputs

    def _check(self) -> None:
        if self.params.by == "extent" and len(self.params.extent) != 4:
            raise ConfigError("l'emprise demande quatre nombres : ouest, sud, est, nord")

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        self._check()
        core.check_raster_schema(inputs.get("input"))
        mask = inputs.get("mask")
        if self.params.by == "features" and mask is not None and GEOM not in mask.names:
            raise ConfigError("les entités « mask » n'ont pas de géométrie")
        return {"output": inputs.get("input") or core.raster_schema()}

    def _clip(
        self, desc: dict[str, Any], geom: shapely.Geometry | None, bounds: tuple[float, float, float, float], stem: str
    ) -> dict[str, Any] | None:
        """Un raster découpé par `geom` (pixels hors géométrie masqués) sur l'emprise `bounds`, ou None si rien en commun."""
        p = self.params
        with core.gdal_env(), rasterio.open(core.gdal_uri(desc["uri"])) as ds:
            if p.crop or geom is None:
                window = core.pixel_window(ds.transform, bounds, ds.width, ds.height)
            else:
                window = Window(0, 0, ds.width, ds.height)
            if window is None:
                return None
            transform = ds.window_transform(window)
        nodata = core.nodata_for(desc["dtype"], desc.get("nodata"))
        grid = {
            "crs": desc["crs"],
            "transform": [transform.a, transform.b, transform.c, transform.d, transform.e, transform.f],
            "width": int(window.width),
            "height": int(window.height),
        }

        def compute(w, cache):
            ds = cache.open(desc["uri"])
            source = Window(window.col_off + w.col_off, window.row_off + w.row_off, w.width, w.height)
            data = ds.read(window=source, masked=True)
            if geom is not None:
                outside = geometry_mask(
                    [geom],
                    out_shape=(int(w.height), int(w.width)),
                    transform=rasterio.windows.transform(w, transform),
                    all_touched=p.all_touched,
                    invert=False,
                )
                data = np.ma.masked_where(np.broadcast_to(outside, data.shape), data)
            return np.ma.filled(data, nodata if nodata is not None else 0).astype(desc["dtype"], copy=False)

        return core.run_blocks(
            self.ctx,
            stem,
            grid,
            compute,
            count=desc["count"],
            dtype=desc["dtype"],
            nodata=nodata,
            band_names=desc["band_names"],
        )

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        self._check()
        p = self.params
        mask = None
        if p.by == "features":
            mask = collect(inputs.get("mask", ()))
            if mask is None or mask.num_rows == 0:
                raise ConfigError("aucune entité « mask » reçue")
            if GEOM not in mask.column_names:
                raise ConfigError("les entités « mask » n'ont pas de géométrie")
        done = skipped = 0
        prepared: dict[str, tuple[list, Any]] = {}  # par SCR de raster : découpeurs reprojetés (et leur union)

        def masks_in(crs: str) -> tuple[list, Any]:
            if crs not in prepared:
                geoms = to_crs(geometries(mask), get_crs(mask), crs)
                rows = mask.select(list(p.mask_attributes)).to_pylist() if p.mask_attributes else None
                live = [(g, rows[i] if rows else {}) for i, g in enumerate(geoms) if g is not None and not g.is_empty]
                union = shapely.union_all([g for g, _ in live]) if p.mode == "union" and live else None
                prepared[crs] = (live, union)
            return prepared[crs]

        for batch in inputs.get("input", ()):
            descs = core.descriptors(batch)
            attributes = core.attributes_of(batch)
            outputs: list[dict[str, Any]] = []
            columns: dict[str, list[Any]] = {k: [] for k in attributes}
            extra: dict[str, list[Any]] = {n: [] for n in p.mask_attributes}
            for i, desc in enumerate(descs):
                self.ctx.check_cancel()
                jobs: list[tuple[shapely.Geometry | None, tuple[float, float, float, float], dict[str, Any]]] = []
                if mask is None:
                    jobs.append((None, tuple(p.extent), {}))  # type: ignore[arg-type]
                else:
                    live, union = masks_in(desc["crs"])
                    if p.mode == "union":
                        if union is not None:
                            jobs.append((union, union.bounds, {}))
                    else:
                        jobs += [(g, g.bounds, {n: r.get(n) for n in p.mask_attributes}) for g, r in live]
                for j, (geom, bounds, copied) in enumerate(jobs):
                    out = self._clip(desc, geom, bounds, f"clip_{i}_{j}")
                    if out is None:
                        skipped += 1
                        continue
                    outputs.append(out)
                    for k in attributes:
                        columns[k].append(attributes[k][i])
                    for n in p.mask_attributes:
                        extra[n].append(copied.get(n))
                    done += 1
            if outputs:
                emit("output", core.to_table(outputs, {**columns, **extra}))
        if skipped:
            self.ctx.warning(f"{skipped} découpe(s) sans recouvrement avec le raster, ignorée(s)")
        self.ctx.info(f"{done} raster(s) découpé(s)")
