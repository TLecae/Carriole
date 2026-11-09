"""DXF (AutoCAD), lu et écrit par GDAL.

Lecture : une seule couche, `entities`. Attributs : `Layer` (calque), `PaperSpace`, `SubClasses`, `Linetype`,
`EntityHandle`, `Text` (texte d'une entité TEXT ou MTEXT, motif d'une hachure). Les blocs (INSERT) sont éclatés en
géométries ; les géométries sont de tous types dans la même couche. Le fichier ne porte pas de système de coordonnées :
il se déclare à la lecture.

Écriture : un DXF n'a pas de table attributaire. Chaque entité reçoit un calque (valeur d'un attribut), un point qui
porte un texte devient une entité TEXT ; les autres attributs ne sont pas écrits (le journal les nomme). GDAL écrit le
fichier d'un bloc : les lots sont gardés jusqu'à la fermeture, sans leurs autres attributs.
"""

from __future__ import annotations

import contextlib
import os
import threading
from collections.abc import Iterator
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyogrio
import pyogrio.raw
import shapely

from etl_engine.atomic import part_name
from etl_engine.data import GEOM, get_crs, has_geometry, with_crs
from etl_engine.formats import ogr
from etl_engine.sdk import ConfigError

LAYER = "Layer"  # attribut du calque, à la lecture comme à l'écriture
DEFAULT_LAYER = "0"  # calque d'une entité sans calque (celui qu'AutoCAD crée toujours)
FORBIDDEN = r'[<>/\\":;?*|=`]'  # caractères refusés dans un nom de calque
_HATCH_LOCK = threading.Lock()  # option DXF_WRITE_HATCH de GDAL, commune au processus


def _open_options(closed_as_polygons: bool) -> dict[str, str]:
    return {"CLOSED_LINE_AS_POLYGON": "YES" if closed_as_polygons else "NO"}


def _layers_where(layers: list[str] | None, where: str | None) -> str | None:
    """Filtre GDAL : calques choisis, puis la condition de l'utilisateur."""
    parts = []
    if layers:
        listing = ", ".join("'" + name.replace("'", "''") + "'" for name in layers)
        parts.append(f'"{LAYER}" IN ({listing})')
    if where and where.strip():
        parts.append(f"({where.strip()})")
    return " AND ".join(parts) or None


def read_schema(path: str, crs: str | None = None, closed_as_polygons: bool = True, **opts: Any) -> pa.Schema:
    schema = ogr.read_schema(path, open_options=_open_options(closed_as_polygons), **opts)
    return with_crs(schema, crs) if crs else schema


def read_batches(
    path: str,
    batch_rows: int,
    crs: str | None = None,
    closed_as_polygons: bool = True,
    layers: list[str] | None = None,
    where: str | None = None,
    **opts: Any,
) -> Iterator[pa.Table]:
    batches = ogr.read_batches(
        path,
        batch_rows,
        where=_layers_where(layers, where),
        open_options=_open_options(closed_as_polygons),
        **opts,
    )
    for batch in batches:
        yield with_crs(batch, crs) if crs else batch


def _texts(column: pa.ChunkedArray | None, rows: int) -> pa.Array:
    if column is None:
        return pa.nulls(rows, pa.string())
    return pc.cast(column, pa.string()).combine_chunks()


def _windows_1252(column: pa.Array) -> tuple[pa.Array, bool]:
    """Textes du jeu Windows-1252, celui que déclare l'en-tête du dessin (GDAL n'y convertit pas les noms de calque) :
    un caractère hors du jeu devient « ? ». Calcul sur les valeurs distinctes, et seulement s'il y a des accents."""
    if pc.all(pc.fill_null(pc.string_is_ascii(column), True)).as_py():
        return column, False
    encoded = column.dictionary_encode()
    values = encoded.dictionary.to_pylist()
    fitted = [v.encode("cp1252", "replace").decode("cp1252") for v in values]
    return pc.take(pa.array(fitted, pa.string()), encoded.indices), fitted != values


class DxfWriter:
    """Écrit un DXF : calque, textes des points, polygones en contours (polylignes fermées) ou en hachures."""

    def __init__(
        self,
        path: str,
        mode: str = "overwrite",
        layer_attribute: str = "",
        text_attribute: str = "",
        text_height: float = 2.5,
        polygons: str = "contour",
        **_: Any,
    ) -> None:
        if mode == "append":
            raise ConfigError("un DXF ne se complète pas : choisir le mode create ou overwrite")
        if mode == "create" and os.path.exists(path):
            raise ConfigError(f"le fichier existe déjà : {path}")
        self.path, self.polygons, self.text_height = path, polygons, float(text_height)
        self.layer_attribute, self.text_attribute = layer_attribute, text_attribute
        self.messages: list[str] = []
        self._parts: list[pa.Table] = []
        self._dropped: dict[str, None] = {}  # attributs non écrits, dans l'ordre d'arrivée
        self._crs: str | None = None

    def _layer_column(self, table: pa.Table) -> str | None:
        if self.layer_attribute:
            if self.layer_attribute not in table.column_names:
                raise ConfigError(f"attribut du calque absent des données : {self.layer_attribute}")
            return self.layer_attribute
        return LAYER if LAYER in table.column_names else None

    def write(self, table: pa.Table) -> None:
        if not has_geometry(table):
            raise ConfigError("un DXF ne contient que des géométries : les données reçues n'en ont pas")
        if self.text_attribute and self.text_attribute not in table.column_names:
            raise ConfigError(f"attribut du texte absent des données : {self.text_attribute}")
        self._crs = self._crs or get_crs(table)
        layer, text = self._layer_column(table), self.text_attribute or None
        kept = {GEOM, *(c for c in (layer, text) if c)}
        for name in table.column_names:
            if name not in kept:
                self._dropped.setdefault(name, None)
        rows = table.num_rows
        self._parts.append(
            pa.table(
                {
                    GEOM: table.column(GEOM),
                    "layer": _texts(table.column(layer) if layer else None, rows),
                    "text": _texts(table.column(text) if text else None, rows),
                }
            )
        )

    def abort(self) -> None:
        self._parts = []

    def close(self) -> None:
        parts, self._parts = self._parts, []
        if not parts:
            return
        table = pa.concat_tables(parts)
        geoms = shapely.from_wkb(table.column(GEOM).to_numpy(zero_copy_only=False))
        present = ~shapely.is_missing(geoms) & ~shapely.is_empty(geoms)
        if not present.all():
            self.messages.append(f"DXF : {int((~present).sum())} entité(s) sans géométrie non écrite(s)")
            table, geoms = table.filter(pa.array(present)), geoms[present]
        layers, replaced = _windows_1252(table.column("layer").combine_chunks())
        text, replaced_text = _windows_1252(table.column("text").combine_chunks())
        if replaced or replaced_text:
            self.messages.append("DXF : caractères hors du jeu Windows-1252 remplacés par « ? »")
        layers = pc.replace_substring_regex(pc.fill_null(layers, DEFAULT_LAYER), FORBIDDEN, "_")
        layers = pc.if_else(pc.equal(layers, ""), DEFAULT_LAYER, layers)
        # un point qui porte un texte devient une entité TEXT (hauteur en unités du dessin)
        escaped = pc.replace_substring(pc.replace_substring(text, "\\", "\\\\"), '"', '\\"')
        labels = pc.binary_join_element_wise('LABEL(f:"Arial",t:"', escaped, f'",s:{self.text_height:g}g)', "")
        labelled = pc.and_(pa.array(shapely.get_type_id(geoms) == 0), pc.greater(pc.utf8_length(text), 0))
        styles = pc.if_else(pc.fill_null(labelled, False), labels, pa.scalar(None, pa.string()))
        target = part_name(self.path)  # tout ou rien : le fichier n'apparaît qu'une fois écrit
        # polygones : hachures, ou polylignes fermées (une par anneau). L'option de GDAL vaut pour tout le processus :
        # posée le temps de cette écriture seulement, une écriture DXF à la fois.
        hatch = "YES" if self.polygons == "hachure" else "NO"
        try:
            with _HATCH_LOCK:
                previous = pyogrio.get_gdal_config_option("DXF_WRITE_HATCH")
                pyogrio.set_gdal_config_options({"DXF_WRITE_HATCH": hatch})
                try:
                    self._write(target, geoms, layers, styles)
                finally:
                    pyogrio.set_gdal_config_options({"DXF_WRITE_HATCH": previous})
            os.replace(target, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(target)
            raise
        if self._dropped:
            listing = ", ".join(self._dropped)
            self.messages.append(f"DXF : attributs non écrits (seuls le calque et le texte le sont) : {listing}")

    def _write(self, target: str, geoms: np.ndarray, layers: pa.Array, styles: pa.Array) -> None:
        """Écriture par GDAL, attribut par attribut (le pilote DXF ne reçoit pas de flux Arrow : ses champs sont
        fixés), en Windows-1252 comme l'en-tête du dessin."""
        pyogrio.raw.write(
            target,
            np.array(shapely.to_wkb(geoms), dtype=object),
            [layers.to_numpy(zero_copy_only=False), styles.to_numpy(zero_copy_only=False)],
            [LAYER, "OGR_STYLE"],
            driver="DXF",
            geometry_type="Unknown",
            crs=self._crs,
            encoding="cp1252",
        )
