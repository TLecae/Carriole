"""Reproject : change le système de coordonnées."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyproj
import shapely
from pydantic import Field

from etl_engine.data import GEOM, crs_object, get_crs, transformer, with_crs
from etl_engine.sdk import GEOMETRY_SLICE, REJECTED, ConfigError, Crs, Modifier, Params, reject


class Reproject(Modifier):
    name = "Reproject"
    label = "Reprojeter"
    category = "Géométrie"
    slice_rows = GEOMETRY_SLICE
    description = "Transforme les géométries dans un autre système de coordonnées."
    help = (
        "Le système d'origine est celui des données ; on peut le forcer si les données n'en déclarent pas (ou un "
        "faux). Les entités sans géométrie passent telles quelles ; une géométrie dont la transformation échoue "
        "(hors du domaine de validité, valeurs infinies) part sur « Rejected »."
    )

    class Params(Params):
        target: Crs = Field("", title="Système cible", description="Ex. EPSG:2154.")
        source: Crs = Field("", title="Système source", description="Vide = celui des données.")

    def _check(self) -> pyproj.CRS:
        if not self.params.target:
            raise ConfigError("système de coordonnées cible non renseigné")
        try:
            return crs_object(self.params.target)
        except pyproj.exceptions.CRSError:
            raise ConfigError(f"système de coordonnées inconnu : {self.params.target}") from None

    def output_schema(self, inputs):
        self._check()
        schema = inputs.get("input")
        if schema is None:
            return {"output": None}
        if GEOM not in schema.names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        return {"output": with_crs(schema, self.params.target)}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        target = self._check()
        if GEOM not in batch.column_names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        source_name = self.params.source or get_crs(batch)
        if not source_name:
            raise ConfigError("les données n'ont pas de système de coordonnées : renseigner « Système source »")
        try:
            source = crs_object(source_name)
        except pyproj.exceptions.CRSError:
            raise ConfigError(f"système de coordonnées inconnu : {source_name}") from None
        if source == target:
            return {"output": with_crs(batch, self.params.target)}

        projection = transformer(source_name, self.params.target)
        geoms = shapely.from_wkb(batch.column(GEOM).to_numpy(zero_copy_only=False))
        present = ~shapely.is_missing(geoms)
        has_z = bool(len(geoms) and shapely.has_z(geoms[present]).any()) if present.any() else False

        def move(coords: np.ndarray) -> np.ndarray:
            if has_z:
                x, y, z = projection.transform(coords[:, 0], coords[:, 1], coords[:, 2])
                return np.column_stack([x, y, z])
            x, y = projection.transform(coords[:, 0], coords[:, 1])
            return np.column_stack([x, y])

        moved = np.array(geoms, dtype=object)
        moved[present] = shapely.transform(geoms[present], move, include_z=has_z)
        finite = np.ones(len(geoms), dtype=bool)
        if present.any():
            bounds = shapely.bounds(moved[present])
            finite[present] = np.isfinite(bounds).all(axis=1) & ~shapely.is_empty(moved[present])
        wkb = pa.array(shapely.to_wkb(moved), pa.binary())
        out = batch.set_column(batch.column_names.index(GEOM), GEOM, wkb)
        good = pa.array(finite | ~present)
        result = {"output": with_crs(out.filter(good), self.params.target)}
        if not good.to_numpy(zero_copy_only=False).all():
            result[REJECTED] = reject(batch.filter(pc.invert(good)), "reprojection impossible")
        return result
