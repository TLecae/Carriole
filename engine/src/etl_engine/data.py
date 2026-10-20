"""Conventions de données entre les nœuds.

Un lot est une `pyarrow.Table`. La géométrie, s'il y en a une, est dans la colonne
`geometry` au format WKB (binaire). Le système de coordonnées est porté par les
métadonnées du schéma (`etl.crs`), et survit à l'écriture Parquet.
"""

from __future__ import annotations

import functools
from collections.abc import Iterable

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyproj
import shapely

GEOM = "geometry"
REJECT_COL = "_rejection_reason"
CRS_KEY = b"etl.crs"
DEFAULT_BATCH_ROWS = 100_000


def get_crs(obj: pa.Table | pa.Schema) -> str | None:
    schema = obj.schema if isinstance(obj, pa.Table) else obj
    value = (schema.metadata or {}).get(CRS_KEY)
    return value.decode() if value else None


def with_crs(obj: pa.Table | pa.Schema, crs: str | None):
    """Copie de `obj` portant le SCR `crs` (inchangé si `crs` est vide)."""
    schema = obj.schema if isinstance(obj, pa.Table) else obj
    if not crs:
        return obj
    meta = dict(schema.metadata or {})
    meta[CRS_KEY] = crs.encode()
    return obj.replace_schema_metadata(meta) if isinstance(obj, pa.Table) else schema.with_metadata(meta)


def has_geometry(obj: pa.Table | pa.Schema) -> bool:
    schema = obj.schema if isinstance(obj, pa.Table) else obj
    return GEOM in schema.names


# Types de géométrie (`shapely.get_type_id`) : à tester d'un coup sur un tableau, jamais entité par entité.
NO_GEOMETRY = -1
POINTS = (0, 4)  # Point, MultiPoint
LINES = (1, 2, 5)  # LineString, LinearRing, MultiLineString
AREAS = (3, 6)  # Polygon, MultiPolygon
COLLECTION = 7


def type_ids(geoms: np.ndarray) -> np.ndarray:
    """Type de chaque géométrie (-1 : absente), vectorisé."""
    return shapely.get_type_id(geoms)


def present(geoms: np.ndarray) -> np.ndarray:
    """Géométrie présente et non vide."""
    return ~(shapely.is_missing(geoms) | shapely.is_empty(geoms))


@functools.lru_cache(maxsize=64)
def is_geographic(crs: str | None) -> bool:
    """Le SCR est-il en degrés ? (mis en cache : la question revient à chaque lot ; faux si inconnu)"""
    if not crs:
        return False
    try:
        return bool(pyproj.CRS.from_user_input(crs).is_geographic)
    except pyproj.exceptions.CRSError:
        return False


def replace_geometries(
    table: pa.Table, geoms: np.ndarray, changed: np.ndarray | None = None, crs: str | None = None
) -> pa.Table:
    """`table` avec la géométrie `geoms` ; si `changed` est donné, seules ces lignes sont réencodées.

    Les autres gardent leur WKB d'origine octet pour octet : rien à encoder, rien à copier en Python.
    """
    if changed is None:
        column = pa.array(shapely.to_wkb(geoms), pa.binary())
    else:
        original = table.column(GEOM).combine_chunks()
        if not changed.any():
            return with_crs(table, crs or get_crs(table))
        new = pa.array(shapely.to_wkb(geoms[changed]), original.type)
        column = pc.replace_with_mask(original, pa.array(changed), new)
    if GEOM in table.column_names:
        table = table.set_column(table.column_names.index(GEOM), GEOM, column)
    else:
        table = table.append_column(GEOM, column)
    return with_crs(table, crs or get_crs(table))


def geometries(table: pa.Table) -> np.ndarray:
    """Tableau d'objets Shapely de la colonne `geometry` (None si nulle)."""
    return shapely.from_wkb(table.column(GEOM).to_numpy(zero_copy_only=False))


def set_geometries(table: pa.Table, geoms: Iterable, crs: str | None = None) -> pa.Table:
    """Remplace (ou ajoute) la colonne `geometry` à partir d'objets Shapely."""
    wkb = pa.array(
        shapely.to_wkb(np.asarray(list(geoms) if not isinstance(geoms, np.ndarray) else geoms, dtype=object)),
        type=pa.binary(),
    )
    if GEOM in table.column_names:
        table = table.set_column(table.column_names.index(GEOM), GEOM, wkb)
    else:
        table = table.append_column(GEOM, wkb)
    return with_crs(table, crs or get_crs(table))


def empty_like(schema: pa.Schema) -> pa.Table:
    return schema.empty_table()


def conform(table: pa.Table, schema: pa.Schema) -> pa.Table:
    """Aligne `table` sur `schema` : colonnes réordonnées, manquantes ajoutées à null, types convertis.

    Lève `ValueError` si une colonne est incompatible : c'est une erreur de modificateur, pas de donnée.
    """
    if table.schema.equals(schema, check_metadata=False):
        return table
    columns = []
    for field in schema:
        if field.name in table.column_names:
            col = table.column(field.name)
            if col.type != field.type:
                try:
                    col = col.cast(field.type)
                except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
                    raise ValueError(f"colonne « {field.name} » : {col.type} incompatible avec {field.type}") from exc
            columns.append(col)
        else:
            columns.append(pa.nulls(table.num_rows, field.type))
    return pa.Table.from_arrays(columns, schema=schema.remove_metadata()).replace_schema_metadata(table.schema.metadata)


def schema_to_json(schema: pa.Schema | None) -> dict | None:
    """Description légère d'un schéma pour l'interface (None = schéma inconnu)."""
    if schema is None:
        return None
    return {
        "fields": [{"name": f.name, "type": str(f.type)} for f in schema if f.name != GEOM],
        "geometry": has_geometry(schema),
        "crs": get_crs(schema),
    }
