"""Écriture de fichiers VRT (rasters virtuels) : sous-ensemble de bandes, empilement, mosaïque.

Un VRT ne contient aucun pixel, seulement des références : c'est le moyen de composer des rasters sans les copier
(`/vsicurl/` compris). GDAL en fait ses sources à la volée.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import rasterio

from etl_engine.raster.core import extent_of, gdal_uri
from etl_engine.sdk import ConfigError

GDAL_TYPES = {
    "uint8": "Byte",
    "int8": "Int8",
    "uint16": "UInt16",
    "int16": "Int16",
    "uint32": "UInt32",
    "int32": "Int32",
    "float32": "Float32",
    "float64": "Float64",
}


def _srs(crs: str | None) -> str:
    return f"  <SRS>{escape(rasterio.crs.CRS.from_string(crs).to_wkt())}</SRS>\n" if crs else ""


def _geotransform(t: Sequence[float]) -> str:
    a, b, c, d, e, f = t
    return f"  <GeoTransform>{c!r}, {a!r}, {b!r}, {f!r}, {d!r}, {e!r}</GeoTransform>\n"


def _band(
    index: int,
    dtype: str,
    nodata: float | None,
    name: str,
    sources: Sequence[str],
) -> str:
    out = f'  <VRTRasterBand dataType="{GDAL_TYPES.get(dtype, "Float32")}" band="{index}">\n'
    if nodata is not None:
        out += f"    <NoDataValue>{'nan' if nodata != nodata else repr(float(nodata))}</NoDataValue>\n"
    if name:
        out += f"    <Description>{escape(name)}</Description>\n"
    out += "".join(sources) + "  </VRTRasterBand>\n"
    return out


def _source(
    desc: dict[str, Any],
    band: int,
    dst: tuple[float, float, float, float],
    nodata: float | None,
    complex_source: bool = False,
) -> str:
    """Le raster `desc` (sa bande `band`) placé dans le rectangle `dst` : x, y, largeur, hauteur en pixels."""
    tag = "ComplexSource" if complex_source else "SimpleSource"
    xml = f"    <{tag}>\n"
    xml += f'      <SourceFilename relativeToVRT="0">{escape(gdal_uri(desc["uri"]))}</SourceFilename>\n'
    xml += f"      <SourceBand>{band}</SourceBand>\n"
    xml += f'      <SrcRect xOff="0" yOff="0" xSize="{desc["width"]}" ySize="{desc["height"]}"/>\n'
    xml += f'      <DstRect xOff="{dst[0]!r}" yOff="{dst[1]!r}" xSize="{dst[2]!r}" ySize="{dst[3]!r}"/>\n'
    if complex_source and desc.get("nodata") is not None:
        value = "nan" if desc["nodata"] != desc["nodata"] else repr(float(desc["nodata"]))
        xml += f"      <NODATA>{value}</NODATA>\n"
    return xml + f"    </{tag}>\n"


def write(path: Path, width: int, height: int, crs: str | None, transform: Sequence[float], bands: str) -> Path:
    path.write_text(
        f'<VRTDataset rasterXSize="{width}" rasterYSize="{height}">\n'
        f"{_srs(crs)}{_geotransform(transform)}{bands}</VRTDataset>\n",
        encoding="utf-8",
    )
    return path


def subset(desc: dict[str, Any], indexes: Sequence[int], names: Sequence[str], path: Path) -> Path:
    """Un VRT avec seulement les bandes `indexes` (à partir de 1) de `desc`, sous les noms `names`."""
    bands = ""
    for out_index, (src_index, name) in enumerate(zip(indexes, names, strict=True), 1):
        source = _source(desc, src_index, (0, 0, desc["width"], desc["height"]), desc.get("nodata"))
        bands += _band(out_index, desc["dtype"], desc.get("nodata"), name, [source])
    return write(path, desc["width"], desc["height"], desc["crs"], desc["transform"], bands)


def stack(entries: Sequence[tuple[dict[str, Any], int, str]], path: Path) -> Path:
    """Un VRT dont chaque bande vient d'un raster (`(descripteur, bande, nom)`), sur la grille du premier ; les autres
    sont étirés sur la même emprise (même système et mêmes limites requis : sinon passer par StackBands)."""
    first = entries[0][0]
    bands = ""
    for out_index, (desc, src_band, name) in enumerate(entries, 1):
        source = _source(desc, src_band, (0, 0, first["width"], first["height"]), desc.get("nodata"))
        bands += _band(out_index, desc["dtype"], desc.get("nodata"), name, [source])
    return write(path, first["width"], first["height"], first["crs"], first["transform"], bands)


def mosaic(
    descs: Sequence[dict[str, Any]], path: Path, names: Sequence[str] | None = None
) -> tuple[Path, dict[str, Any]]:
    """Mosaïque spatiale : tous les rasters (même système, même pas) sur leur emprise commune ; le dernier recouvre
    les précédents là où il a des données."""
    first = descs[0]
    if any(d["crs"] != first["crs"] for d in descs):
        raise ConfigError("la mosaïque demande le même système de coordonnées : reprojeter d'abord")
    a, b, _, d_, e, _ = first["transform"]
    if b or d_:
        raise ConfigError("la mosaïque ne gère pas les rasters pivotés")
    for d in descs:
        if abs(d["transform"][0] - a) > abs(a) * 1e-6 or abs(d["transform"][4] - e) > abs(e) * 1e-6:
            raise ConfigError("la mosaïque demande le même pas de pixel : rééchantillonner d'abord")
    boxes = [extent_of(d) for d in descs]
    xmin, ymin = min(bx[0] for bx in boxes), min(bx[1] for bx in boxes)
    xmax, ymax = max(bx[2] for bx in boxes), max(bx[3] for bx in boxes)
    width, height = round((xmax - xmin) / abs(a)), round((ymax - ymin) / abs(e))
    count = min(d["count"] for d in descs)
    if count != max(d["count"] for d in descs):
        raise ConfigError("la mosaïque demande le même nombre de bandes")
    top = ymax if e < 0 else ymin
    transform = [a, 0.0, xmin, 0.0, e, top]
    nodata = first.get("nodata")
    bands = ""
    for index in range(1, count + 1):
        sources = []
        for d in descs:
            x0 = round((d["transform"][2] - xmin) / abs(a))
            y0 = round((top - d["transform"][5]) / abs(e))
            sources.append(_source(d, index, (x0, y0, d["width"], d["height"]), nodata, complex_source=True))
        name = (names[index - 1] if names and index <= len(names) else "") or (
            first["band_names"][index - 1] if index <= len(first["band_names"]) else ""
        )
        bands += _band(index, first["dtype"], nodata, name, sources)
    write(path, width, height, first["crs"], transform, bands)
    return path, {
        **{k: first[k] for k in ("driver", "dtype", "nodata", "crs")},
        "uri": str(path),
        "driver": "VRT",
        "width": width,
        "height": height,
        "count": count,
        "transform": transform,
        "band_names": list(first["band_names"][:count]),
    }
