"""Aides pour les modificateurs bloquants (qui ont besoin de toutes les données) : collecte et requêtes DuckDB."""

from __future__ import annotations

from collections.abc import Iterable

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from etl_engine import duck as duck_
from etl_engine.data import GEOM, get_crs, with_crs
from etl_engine.sdk import ConfigError


def quote(name: str) -> str:
    """Identifiant SQL entre guillemets."""
    return '"' + name.replace('"', '""') + '"'


def collect(batches: Iterable[pa.Table], schema: pa.Schema | None = None) -> pa.Table | None:
    """Tous les lots d'une entrée en une table (None si l'entrée n'a rien envoyé et que le schéma est inconnu)."""
    parts = list(batches)
    if not parts:
        return schema.empty_table() if schema is not None else None
    table = pa.concat_tables(parts, promote_options="default") if len(parts) > 1 else parts[0]
    return with_crs(table, get_crs(parts[0]))


def _column_codes(column: pa.ChunkedArray | pa.Array) -> np.ndarray:
    """Un entier par valeur distincte (nul compris) ; vectorisé, sauf types que Arrow ne sait pas encoder."""
    array = column.combine_chunks() if isinstance(column, pa.ChunkedArray) else column
    try:
        encoded = pc.dictionary_encode(array, null_encoding="encode")
        return encoded.indices.to_numpy(zero_copy_only=False).astype(np.int64)
    except (pa.ArrowNotImplementedError, pa.ArrowInvalid, pa.ArrowTypeError):
        seen: dict[str, int] = {}
        return np.array([seen.setdefault(repr(v), len(seen)) for v in array.to_pylist()], dtype=np.int64)


def group_codes(table: pa.Table, columns: list[str]) -> tuple[np.ndarray, int]:
    """(numéro de groupe de chaque ligne, nombre de groupes) ; groupes numérotés dans l'ordre de première apparition.

    Sans colonne : un seul groupe. Deux lignes sont du même groupe si elles ont les mêmes valeurs (nul = nul).
    """
    n = table.num_rows
    if not columns:
        return np.zeros(n, dtype=np.int64), (1 if n else 0)
    if not n:
        return np.zeros(0, dtype=np.int64), 0
    parts = [_column_codes(table.column(c)) for c in columns]
    if len(parts) == 1:
        _, first, inverse = np.unique(parts[0], return_index=True, return_inverse=True)
    else:
        _, first, inverse = np.unique(np.stack(parts, axis=1), axis=0, return_index=True, return_inverse=True)
    rank = np.empty(len(first), dtype=np.int64)
    rank[np.argsort(first, kind="stable")] = np.arange(len(first))
    return rank[inverse.ravel()], len(first)


def group_slices(codes: np.ndarray, count: int) -> tuple[np.ndarray, np.ndarray]:
    """(lignes triées par groupe, bornes) : les lignes du groupe g sont `order[bounds[g]:bounds[g + 1]]`, dans leur
    ordre d'origine."""
    order = np.argsort(codes, kind="stable")
    bounds = np.searchsorted(codes[order], np.arange(count + 1))
    return order, bounds


def group_ranks(codes: np.ndarray, count: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(rang de chaque ligne dans son groupe à partir de 0, première ligne de chaque groupe, taille de chaque groupe),
    dans l'ordre d'origine des lignes."""
    order, bounds = group_slices(codes, count)
    sizes = np.diff(bounds)
    rank = np.empty(len(codes), dtype=np.int64)
    rank[order] = np.arange(len(codes)) - np.repeat(bounds[:-1], sizes)
    return rank, order[bounds[:-1]], sizes


def group_keys(table: pa.Table, columns: list[str], rows: np.ndarray) -> list[tuple]:
    """Valeurs des `columns` aux lignes `rows`, une clé par ligne, utilisable dans un dictionnaire (une liste ou une
    structure est comparée par son texte)."""
    values = [table.column(c).take(pa.array(rows, pa.int64())).to_pylist() for c in columns]
    return [tuple(v if not isinstance(v, (list, dict)) else repr(v) for v in key) for key in zip(*values, strict=True)]


def duck(sql: str, **tables: pa.Table) -> pa.Table:
    """Exécute `sql` dans un DuckDB éphémère où chaque argument nommé est une table Arrow."""
    con = duck_.connect("heavy")
    try:
        for name, table in tables.items():
            con.register(name, table)
        return con.sql(sql).to_arrow_table()
    except duckdb.Error as exc:
        if (found := duck_.resource_error(exc)) is not None:
            raise found from exc
        raise
    finally:
        con.close()


def match_pairs(base: pa.Table, other: pa.Table, base_keys: list[str], other_keys: list[str]) -> pa.Table:
    """Paires (bi, ji) d'indices de lignes qui ont les mêmes clés (une clé nulle ne s'apparie jamais).

    Si les types des clés diffèrent (ex. entier et texte), la comparaison se fait en texte.
    """
    if len(base_keys) != len(other_keys) or not base_keys:
        raise ValueError("autant de clés de chaque côté, au moins une")
    left = pa.table({f"k{i}": base.column(k) for i, k in enumerate(base_keys)})
    right = pa.table({f"k{i}": other.column(k) for i, k in enumerate(other_keys)})
    conditions = []
    for i, (a, b) in enumerate(zip(base_keys, other_keys, strict=True)):
        same = base.schema.field(a).type == other.schema.field(b).type
        conditions.append(f"b.k{i} = j.k{i}" if same else f"CAST(b.k{i} AS VARCHAR) = CAST(j.k{i} AS VARCHAR)")
    left = left.append_column("bi", pa.array(range(left.num_rows), pa.int64()))
    right = right.append_column("ji", pa.array(range(right.num_rows), pa.int64()))
    return duck(f"SELECT b.bi, j.ji FROM b JOIN j ON {' AND '.join(conditions)} ORDER BY b.bi, j.ji", b=left, j=right)


def join_result(
    base: pa.Table,
    other: pa.Table,
    pairs: pa.Table,
    attributes: list[str],
    prefix: str = "",
    conflict: str = "other",
    geometry: str = "base",
    on_multiple: str = "all",
) -> tuple[pa.Table, pa.Table, np.ndarray, np.ndarray]:
    """Assemble les lignes appariées. Renvoie (résultat, indices de base non appariés, bi, ji retenus).

    - `attributes` : colonnes de `other` à recopier (préfixées par `prefix`) ;
    - `conflict` : `other` ou `base` l'emporte quand un nom existe déjà ;
    - `geometry` : `base` (garde), `other` (prend celle de `other`) ou `none` (retire) ;
    - `on_multiple` : `all` (une ligne par correspondance), `first`, ou `error`.
    """
    bi = pairs.column("bi").to_numpy()
    ji = pairs.column("ji").to_numpy()
    if len(bi) and on_multiple != "all":
        _, first = np.unique(bi, return_index=True)
        if on_multiple == "error" and len(first) != len(bi):
            raise ConfigError("plusieurs correspondances pour une même entité (choisir « première » ou « toutes »)")
        bi, ji = bi[first], ji[first]
    matched = base.take(pa.array(bi, pa.int64()))
    source = other.take(pa.array(ji, pa.int64()))
    result = matched
    for attr in attributes:
        name = prefix + attr
        if name == GEOM:
            continue
        column = source.column(attr)
        if name in result.column_names:
            if conflict == "base":
                continue
            result = result.set_column(result.column_names.index(name), name, column)
        else:
            result = result.append_column(name, column)
    if geometry == "other" and GEOM in source.column_names:
        column = source.column(GEOM)
        if GEOM in result.column_names:
            result = result.set_column(result.column_names.index(GEOM), GEOM, column)
        else:
            result = result.append_column(GEOM, column)
    elif geometry == "none" and GEOM in result.column_names:
        result = result.drop_columns([GEOM])
    unmatched = np.setdiff1d(np.arange(base.num_rows), bi, assume_unique=False)
    crs = get_crs(other) if geometry == "other" and GEOM in other.column_names else get_crs(base)
    return with_crs(result, crs), unmatched, bi, ji
