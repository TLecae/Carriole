"""STACSearch : cherche des images dans un catalogue STAC (Sentinel-2, Landsat, MNT…) et les référence."""

from __future__ import annotations

import json
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal

import pyarrow as pa
import shapely
from pydantic import BaseModel, Field

from etl_engine.blocking import collect
from etl_engine.data import GEOM, geometries, get_crs
from etl_engine.raster import core, vrt
from etl_engine.sdk import AttributeList, ConfigError, Emit, Modifier, Params

SIGN_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/sign"


def sign_href(href: str) -> str:
    """Adresse signée d'un asset Planetary Computer (jeton de lecture temporaire)."""
    import httpx

    response = httpx.get(SIGN_URL, params={"href": href}, timeout=30, follow_redirects=True)
    response.raise_for_status()
    return str(response.json()["href"])


class STACSearch(Modifier):
    name = "STACSearch"
    reusable = "partial"  # relu à chaque run, repris dans un run « jusqu'au nœud » (Modifier.reusable)
    label = "Recherche STAC"
    category = "Rasters"
    description = (
        "Cherche des images dans un catalogue STAC (Sentinel-2, Landsat, MNT…) et les référence sans les télécharger."
    )
    help = (
        "Interroge l'API STAC « Adresse du catalogue » (Earth Search d'Element 84 par défaut ; Planetary Computer : "
        "https://planetarycomputer.microsoft.com/api/stac/v1 avec la signature activée). Filtres : collections, emprise "
        '(longitude/latitude WGS84), dates (2024-06-01/2024-06-30), propriétés (JSON, ex. {"eo:cloud_cover": {"lt": 20}}). '
        "Avec « Géométries reçues », chaque entité de l'entrée donne une recherche (son emprise) et ses attributs sont "
        "recopiés. « Assets » liste les bandes voulues (ex. red, nir : leurs noms dans le catalogue) : chaque scène devient "
        "un raster virtuel dont les bandes sont ces assets, lus à la demande en HTTP (rien n'est téléchargé en entier). "
        "Chaque entité porte l'identifiant de la scène, sa date, sa collection et les propriétés demandées. Le proxy "
        "du poste est utilisé."
    )
    inputs = ()
    rejected = False

    class Params(Params):
        api_url: str = Field(
            "https://earth-search.aws.element84.com/v1",
            title="Adresse du catalogue",
            description="Racine de l'API STAC.",
        )
        collections: list[str] = Field(
            default_factory=lambda: ["sentinel-2-l2a"],
            title="Collections",
            description="Collections à chercher.",
            json_schema_extra={"default": ["sentinel-2-l2a"]},
        )
        bbox: list[float] = Field(
            default_factory=list,
            title="Emprise (WGS84)",
            description="Ouest, sud, est, nord en degrés ; vide = pas de filtre (ou géométries reçues).",
            examples=[[1.35, 43.5, 1.55, 43.7]],
        )
        from_input: bool = Field(
            False, title="Géométries reçues", description="Une recherche par entité de l'entrée, sur son emprise."
        )
        copy_attributes: AttributeList = Field(
            default_factory=list,
            title="Attributs à recopier",
            description="Attributs de l'entité reçue ajoutés aux scènes trouvées.",
        )
        datetime: str = Field(
            "",
            title="Dates",
            description="Une date ou un intervalle : 2024-06-01/2024-06-30 ; vide = toutes.",
            examples=["2024-06-01/2024-06-30"],
        )
        query: str = Field(
            "", title="Propriétés (JSON)", description='Filtre sur les propriétés, ex. {"eo:cloud_cover": {"lt": 20}}.'
        )
        max_items: int = Field(50, ge=1, le=5000, title="Nombre maximal de scènes", description="Par recherche.")
        sortby: str = Field(
            "-properties.datetime",
            title="Tri",
            description="Champ de tri, précédé de - pour décroissant.",
            examples=["-properties.datetime"],
        )
        assets: list[str] = Field(
            default_factory=list,
            title="Assets (bandes)",
            description="Noms des assets à garder, dans l'ordre : ils deviennent les bandes du raster.",
            examples=[["red", "nir"]],
        )
        properties: list[str] = Field(
            default_factory=lambda: ["eo:cloud_cover"],
            title="Propriétés à recopier",
            description="Propriétés de la scène ajoutées comme attributs (eo:cloud_cover devient cloud_cover).",
            json_schema_extra={"default": ["eo:cloud_cover"]},
        )
        sign: Literal["none", "planetary_computer"] = Field(
            "none",
            title="Signature",
            description="planetary_computer : signe les adresses (jeton de lecture temporaire).",
        )

    @classmethod
    def ports(cls, params: BaseModel | None = None) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return (("input",) if getattr(params, "from_input", False) else ()), ("output",)

    def _check(self) -> None:
        p = self.params
        if not p.api_url.lower().startswith(("http://", "https://")):
            raise ConfigError("l'adresse du catalogue doit commencer par http:// ou https://")
        if not p.assets:
            raise ConfigError("au moins un asset est nécessaire (par exemple red)")
        if p.bbox and len(p.bbox) != 4:
            raise ConfigError("l'emprise demande quatre nombres : ouest, sud, est, nord")
        if p.query.strip():
            try:
                if not isinstance(json.loads(p.query), dict):
                    raise ValueError
            except ValueError:
                raise ConfigError("les propriétés doivent être un objet JSON") from None

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        self._check()
        p = self.params
        extra = [
            pa.field("stac_id", pa.string()),
            pa.field("collection", pa.string()),
            pa.field("datetime", pa.string()),
        ]
        extra += [pa.field(_attribute(name), pa.float64()) for name in p.properties]
        upstream = inputs.get("input")
        if p.from_input and upstream is not None:
            extra += [upstream.field(a) for a in p.copy_attributes if a in upstream.names]
        return {"output": core.raster_schema(extra)}

    # --- recherche -------------------------------------------------------------------------
    def _search(self, **filters: Any) -> list[Any]:
        from pystac_client import Client

        p = self.params
        try:
            with core.gdal_env():
                client = Client.open(p.api_url)
                search = client.search(
                    collections=p.collections or None,
                    datetime=p.datetime or None,
                    query=json.loads(p.query) if p.query.strip() else None,
                    max_items=p.max_items,
                    limit=min(p.max_items, 200),  # taille de page
                    sortby=p.sortby or None,
                    **filters,
                )
                return list(search.items())
        except Exception as exc:  # noqa: BLE001 - réseau, réponse STAC invalide…
            raise ConfigError(f"recherche STAC impossible ({p.api_url}) : {exc}") from exc

    def _href(self, item: Any, name: str) -> str:
        asset = item.assets.get(name)
        if asset is None:
            raise ConfigError(
                f"asset « {name} » absent de la scène {item.id} (assets : {', '.join(sorted(item.assets))})"
            )
        href = str(asset.href)
        return sign_href(href) if self.params.sign == "planetary_computer" and href.startswith("http") else href

    def _raster_of(self, item: Any) -> dict[str, Any]:
        p = self.params
        descs = [core.describe(self._href(item, name)) for name in p.assets]
        path = core.output_path(self.ctx, item.id, ".vrt")
        first = descs[0]
        entries = [(d, 1, name) for d, name in zip(descs, p.assets, strict=True)]
        if any(d["crs"] != first["crs"] for d in descs):
            raise ConfigError(
                f"la scène {item.id} mêle des systèmes de coordonnées : choisir des assets de même grille"
            )
        vrt.stack(entries, path)
        return core.describe(str(path), list(p.assets))

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        self._check()
        p = self.params
        searches: list[tuple[dict[str, Any], dict[str, Any]]] = []  # (filtres de recherche, attributs recopiés)
        if p.from_input:
            table = collect(inputs.get("input", ()))
            if table is None or table.num_rows == 0 or GEOM not in table.column_names:
                raise ConfigError("aucune géométrie reçue pour la recherche")
            from pyproj import Transformer as ProjModifier

            crs = get_crs(table)
            to_wgs84 = ProjModifier.from_crs(crs, "EPSG:4326", always_xy=True).transform if crs else None
            copied = table.select(p.copy_attributes).to_pylist() if p.copy_attributes else [{}] * table.num_rows
            for geom, attrs in zip(geometries(table), copied, strict=True):
                if geom is None or geom.is_empty:
                    continue
                if to_wgs84:
                    geom = shapely.ops.transform(to_wgs84, geom)
                searches.append(({"intersects": shapely.geometry.mapping(geom)}, attrs))
        else:
            searches.append(({"bbox": p.bbox} if p.bbox else {}, {}))

        scenes: list[tuple[Any, dict[str, Any]]] = []
        for filters, attrs in searches:
            self.ctx.check_cancel()
            found = self._search(**filters)
            self.ctx.info(f"{len(found)} scène(s) trouvée(s)")
            scenes += [(item, attrs) for item in found]
        if not scenes:
            self.ctx.warning("aucune scène ne correspond à la recherche")
            return
        with ThreadPoolExecutor(max_workers=min(8, len(scenes))) as pool:
            rasters = list(pool.map(lambda s: self._raster_of(s[0]), scenes))
        columns: dict[str, list[Any]] = {
            "stac_id": [item.id for item, _ in scenes],
            "collection": [item.collection_id for item, _ in scenes],
            "datetime": [item.datetime.isoformat() if item.datetime else None for item, _ in scenes],
        }
        for name in p.properties:
            columns[_attribute(name)] = [_number(item.properties.get(name)) for item, _ in scenes]
        for name in p.copy_attributes:
            if p.from_input:
                columns[name] = [attrs.get(name) for _, attrs in scenes]
        emit("output", core.to_table(rasters, columns))


def _attribute(name: str) -> str:
    return name.split(":", 1)[-1].replace(":", "_").replace("-", "_")


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
