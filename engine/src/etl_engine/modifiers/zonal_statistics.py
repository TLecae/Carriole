"""ZonalStatistics : statistiques d'un raster sous chaque polygone (moyenne par commune, surface par classe…)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import numpy as np
import pyarrow as pa
import rasterio
from pydantic import Field
from rasterio.features import geometry_mask

from etl_engine.blocking import collect
from etl_engine.data import GEOM, geometries, get_crs, with_crs
from etl_engine.modifiers.clip_raster_by_mask import to_crs
from etl_engine.raster import core
from etl_engine.sdk import AttributeList, ConfigError, Emit, Modifier, Params, ordered_map

ZONES_PER_TASK = 500  # zones traitées d'un bloc par un thread (une ouverture du raster par bloc)

Stat = Literal["count", "sum", "mean", "min", "max", "std", "median", "majority", "unique", "area"]


def summarize(values: np.ndarray, stats: list[str], cell_area: float) -> dict[str, float | None]:
    """Les statistiques demandées sur les valeurs valides (tableau 1D) ; vide = tout à None sauf count/area (0)."""
    n = int(values.size)
    out: dict[str, float | None] = {}
    for stat in stats:
        if stat == "count":
            out[stat] = float(n)
        elif stat == "area":
            out[stat] = n * cell_area
        elif n == 0:
            out[stat] = None
        elif stat == "sum":
            out[stat] = float(values.sum(dtype=np.float64))
        elif stat == "mean":
            out[stat] = float(values.mean(dtype=np.float64))
        elif stat == "min":
            out[stat] = float(values.min())
        elif stat == "max":
            out[stat] = float(values.max())
        elif stat == "std":
            out[stat] = float(values.std(dtype=np.float64))
        elif stat == "median":
            out[stat] = float(np.median(values))
        elif stat == "majority":
            uniques, counts = np.unique(values, return_counts=True)
            out[stat] = float(uniques[np.argmax(counts)])
        else:
            out[stat] = float(np.unique(values).size)
    return out


class ZonalStatistics(Modifier):
    name = "ZonalStatistics"
    label = "Statistiques zonales"
    category = "Rasters"
    description = (
        "Calcule des statistiques d'un raster sous chaque polygone : moyenne, somme, minimum, maximum, majoritaire…"
    )
    help = (
        "Chaque polygone de « zones » reçoit les statistiques des pixels du raster qu'il recouvre (un pixel compte s'il "
        "a son centre dans le polygone, ou s'il est touché avec « Pixels touchés »). Les colonnes s'appellent "
        "`bande_statistique` (par exemple ndvi_mean). Statistiques : count, sum, mean, min, max, std, median, majority "
        "(valeur la plus fréquente : classes d'occupation du sol), unique (nombre de valeurs distinctes), area (surface "
        "des pixels valides, dans l'unité du système au carré). Les pixels sans donnée sont ignorés. S'il y a "
        "plusieurs rasters (dates, scènes), le résultat a une ligne par zone et par raster, avec les « Attributs du "
        "raster à ajouter » (par exemple la date). Un polygone hors du raster a un compte à 0 et des statistiques vides. "
        "Les polygones sont reprojetés dans le système du raster."
    )
    inputs = ("zones", "raster")
    rejected = False

    class Params(Params):
        bands: list[str] = Field(
            default_factory=list, title="Bandes", description="Numéros (de 1) ou noms des bandes ; vide = la première."
        )
        stats: list[Stat] = Field(
            default_factory=lambda: ["mean"],
            title="Statistiques",
            description="count, sum, mean, min, max, std, median, majority, unique ou area.",
            json_schema_extra={"default": ["mean"]},
        )
        all_touched: bool = Field(
            False, title="Pixels touchés", description="Compte tout pixel que le polygone touche."
        )
        raster_attributes: AttributeList = Field(
            default_factory=list,
            title="Attributs du raster à ajouter",
            description="Recopiés sur chaque ligne (la date d'une scène…).",
        )
        prefix: str = Field("", title="Préfixe", description="Ajouté devant le nom de chaque colonne de statistique.")

    def _check(self) -> None:
        if not self.params.stats:
            raise ConfigError("au moins une statistique est nécessaire")

    def _band_indexes(self, desc: dict[str, Any]) -> list[tuple[int, str]]:
        wanted = self.params.bands or ["1"]
        out = []
        for w in wanted:
            names = desc["band_names"]
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

    def _columns(self, band_names: list[str]) -> list[str]:
        p = self.params
        return [f"{p.prefix}{band}_{stat}" for band in band_names for stat in p.stats]

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        self._check()
        zones, raster = inputs.get("zones"), inputs.get("raster")
        core.check_raster_schema(raster)
        if zones is None:
            return {"output": None}
        if GEOM not in zones.names:
            raise ConfigError("les entités « zones » n'ont pas de géométrie")
        if not self.params.bands:
            return {"output": None}  # les noms des bandes ne sont connus qu'à l'exécution : schéma inconnu
        extra = [
            pa.field(c, pa.float64())
            for c in self._columns([n if not n.isdigit() else f"b{n}" for n in self.params.bands])
        ]
        if raster is not None:
            extra += [raster.field(a) for a in self.params.raster_attributes if a in raster.names]
        out = pa.schema([*zones, *extra], metadata=zones.metadata)
        return {"output": out}

    def _zone(self, ds: Any, geom: Any, bands: list, cell_area: float, names: list[str]) -> dict[str, float | None]:
        """Statistiques d'une zone sur un raster ouvert."""
        p = self.params
        row: dict[str, float | None] = {name: None for name in names}
        row.update({f"{p.prefix}{b}_count": 0.0 for _, b in bands if "count" in p.stats})
        row.update({f"{p.prefix}{b}_area": 0.0 for _, b in bands if "area" in p.stats})
        if geom is None or geom.is_empty:
            return row
        window = core.pixel_window(ds.transform, geom.bounds, ds.width, ds.height)
        if window is None:
            return row
        inside = ~geometry_mask(
            [geom],
            out_shape=(int(window.height), int(window.width)),
            transform=ds.window_transform(window),
            all_touched=p.all_touched,
        )
        data = ds.read([i for i, _ in bands], window=window, masked=True)
        for k, (_, band) in enumerate(bands):
            keep = inside & ~np.ma.getmaskarray(data[k])
            stats = summarize(np.asarray(data[k].data)[keep].astype("float64"), list(p.stats), cell_area)
            row.update({f"{p.prefix}{band}_{s}": v for s, v in stats.items()})
        return row

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        self._check()
        p = self.params
        zones = collect(inputs.get("zones", ()))
        rasters = collect(inputs.get("raster", ()))
        if zones is None or zones.num_rows == 0:
            return
        if GEOM not in zones.column_names:
            raise ConfigError("les entités « zones » n'ont pas de géométrie")
        if rasters is None or rasters.num_rows == 0:
            self.ctx.warning("aucun raster reçu ; rien n'est produit")
            return
        descs = core.descriptors(rasters)
        attrs = core.attributes_of(rasters)
        missing = [a for a in p.raster_attributes if a not in attrs]
        if missing:
            raise ConfigError(f"attributs absents : {', '.join(missing)}")
        zone_geoms = geometries(zones)
        for r, desc in enumerate(descs):
            self.ctx.check_cancel()
            bands = self._band_indexes(desc)
            geoms = to_crs(zone_geoms, get_crs(zones), desc["crs"])
            cell_area = abs(desc["transform"][0] * desc["transform"][4])
            names = self._columns([b for _, b in bands])

            def measure(part: np.ndarray, desc=desc, bands=bands, cell_area=cell_area, names=names) -> list[dict]:
                # un jeu de données par thread : rasterio n'autorise pas la lecture concurrente d'un même jeu
                rows = []
                with core.gdal_env(), rasterio.open(core.gdal_uri(desc["uri"])) as ds:
                    for geom in part:
                        rows.append(self._zone(ds, geom, bands, cell_area, names))
                return rows

            parts = np.array_split(geoms, max(1, -(-len(geoms) // ZONES_PER_TASK)))
            results: dict[str, list] = {name: [] for name in names}
            for rows in ordered_map(measure, parts, self.ctx.workers, self.ctx):
                for row in rows:
                    for name in names:
                        results[name].append(row[name])
            columns = {name: pa.array(values, pa.float64()) for name, values in results.items()}
            base = zones
            for name, column in columns.items():
                base = base.append_column(name, column)
            for a in p.raster_attributes:
                base = base.append_column(a, pa.array([attrs[a][r]] * zones.num_rows))
            emit("output", with_crs(base, get_crs(zones)))  # raster par raster : pas d'accumulation
        self.ctx.info(f"{zones.num_rows} zone(s) × {len(descs)} raster(s)")
