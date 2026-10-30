"""Voies d'exécution par DuckDB : un modificateur qui sait s'exprimer en SQL est exécuté directement sur les ports
(voir ADR 0003, 0013, 0044).

- **Port écrit** : la requête est exécutée et ses résultats écrits dans le fichier du port. DuckDB parallélise,
  lit et écrit en flux, et **déborde sur disque** quand la mémoire autorisée est atteinte (tri, jointure, agrégation
  de volumes supérieurs à la mémoire).
- **Port défini** (que rien ne lit, ou d'un modificateur ligne à ligne) : rien n'est écrit. La requête est contrôlée
  sur toutes les lignes et comptée, puis gardée comme définition du port, calculée quand on le lit.

Les entrées sont des fichiers ou les requêtes de ports différés de l'amont (`cache.Relation`) : une chaîne de nœuds
SQL forme une seule requête. La voie par lots (Python) reste la référence : un modificateur qui ne propose pas de SQL,
ou dont les paramètres ou les données ne s'y prêtent pas, s'exécute par elle, avec les mêmes résultats.
"""

from __future__ import annotations

import contextlib
import shutil
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pyarrow as pa

from etl_engine import duck
from etl_engine.cache import Relation, needs_spatial, sql_text, write_query
from etl_engine.data import get_crs
from etl_engine.expr import quote, unguard
from etl_engine.sdk import Cancelled, ConfigError, Modifier

Source = Path | Relation  # un port de l'amont : son fichier, ou la requête qui le lit


@dataclass
class SqlRouteResult:
    rows: dict[str, int] = field(default_factory=dict)
    queries: dict[str, str] = field(default_factory=dict)  # port défini -> sa requête, sur les relations d'entrée
    relations: dict[str, str] = field(default_factory=dict)  # port d'entrée -> nom de sa relation dans les requêtes


def has_sql_route(inst: Modifier) -> bool:
    return type(inst).sql is not Modifier.sql


def row_wise(inst: Modifier) -> bool:
    """Modificateur qui traite les lignes une à une, sur des entrées : ses ports sont définis par leur requête (ADR
    0044), et la voie par lots fait foi pour lui (entités en erreur sur `Rejected`, types prévus par
    `output_schema`)."""
    return inst.mode == "streaming" and bool(inst.ports(inst.params)[0])


def _view(sources: list[Source]) -> str:
    """Les ports réunis en une relation (colonnes réunies par nom)."""
    files = [s for s in sources if isinstance(s, Path)]
    parts = [f"SELECT * FROM ({s.sql})" for s in sources if isinstance(s, Relation)]
    if files:
        listing = ", ".join(sql_text(f) for f in files)
        parts.insert(0, f"SELECT * FROM read_parquet([{listing}], union_by_name=true)")
    return " UNION ALL BY NAME ".join(parts)


@contextlib.contextmanager
def _session(
    inst: Modifier,
    inputs: dict[str, list[Source]],
    relations: dict[str, str],
    queries: dict[str, str],
    spill_dir: Path,
    cancel: threading.Event | None,
) -> Iterator[tuple[duckdb.DuckDBPyConnection, int] | None]:
    """Connexion prête : préparée par le modificateur, entrées en vues, réglée sur la largeur des entrées ; et la
    taille des blocs qu'elle écrit (`duck.fit`). None : `sql_prepare` a échoué sur un modificateur qui accepte le repli
    (`sql_fallback`). Un run annulé interrompt la requête en cours."""
    con = duck.connect("heavy", spill_dir)  # part du budget du run ; au-delà, DuckDB déborde sur disque
    try:
        with duck.interrupted_when(con, cancel.is_set if cancel is not None else None):
            try:
                upstream = [s.sql for parts in inputs.values() for s in parts if isinstance(s, Relation)]
                if any(needs_spatial(q) for q in [*upstream, *queries.values()]):
                    duck.load_spatial(con)
                inst.sql_prepare(con)
            except duckdb.Error as exc:
                if not inst.sql_fallback:
                    raise ConfigError(f"SQL ({inst.name}) : {str(exc).splitlines()[0]}") from None
                yield None
                return
            for port, parts in inputs.items():
                con.execute(f"CREATE VIEW {relations[port]} AS {_view(parts)}")
            files = [f for parts in inputs.values() for s in parts for f in ([s] if isinstance(s, Path) else s.files)]
            block_rows = duck.fit(con, files)
            if inputs:  # modificateur qui lit des ports : ses requêtes (expressions d'utilisateur) n'atteignent qu'eux
                duck.confine(con, [spill_dir.parent], files)
            yield con, block_rows
    finally:
        con.close()
        shutil.rmtree(spill_dir, ignore_errors=True)


@contextlib.contextmanager
def _duckdb_errors(fallback: bool, name: str) -> Iterator[list[bool]]:
    """Erreurs de DuckDB rendues comme celles du moteur ; `failed[0]` vrai si la voie renonce (repli). Mémoire ou disque
    plein : erreur du nœud, sans repli (la voie par lots en manquerait aussi)."""
    failed = [False]
    try:
        yield failed
    except duckdb.InterruptException:
        raise Cancelled from None
    except duckdb.Error as exc:
        if (found := duck.resource_error(exc)) is not None:
            raise found from None
        if not fallback:
            raise ConfigError(f"SQL ({name}) : {str(exc).splitlines()[0]}") from None
        failed[0] = True


def _kind(t: pa.DataType) -> pa.DataType:
    """Type comparé : un texte reste un texte quel que soit son encodage Arrow (dictionnaire, grande taille)."""
    if pa.types.is_dictionary(t):
        t = t.value_type
    if pa.types.is_large_string(t):
        return pa.string()
    if pa.types.is_large_binary(t):
        return pa.binary()
    return t


def same_types(actual: pa.Schema, planned: pa.Schema | None) -> bool:
    """Mêmes colonnes, dans le même ordre, de mêmes types (à l'encodage près) que le schéma prévu."""
    if planned is None or actual.names != planned.names:
        return False
    return all(_kind(a.type) == _kind(p.type) for a, p in zip(actual, planned, strict=True))


def run_sql_route(
    inst: Modifier,
    inputs: dict[str, list[Source]],
    out_paths: dict[str, Path | None],
    out_schemas: dict[str, pa.Schema | None],
    in_schemas: dict[str, pa.Schema | None],
    spill_dir: Path,
    cancel: threading.Event | None = None,
    define: bool = False,
) -> SqlRouteResult | None:
    """Exécute `inst` en SQL. Un port qui a un chemin (`out_paths`) est écrit. Un port sans chemin est contrôlé sur
    toutes les lignes (`count(*)`, plus `count(colonne)` pour les colonnes de `sql_checked`) et compté ; avec `define`,
    sa requête est gardée comme définition (`SqlRouteResult.queries`), calculée quand on le lit (ADR 0044).

    None : la voie par lots s'en charge, avec les mêmes résultats (pas de SQL pour ces réglages, entrée vide, DuckDB
    échoue sur un modificateur qui accepte le repli ; nœud ligne à ligne : erreur sur une donnée, types autres que
    ceux prévus)."""
    if not has_sql_route(inst) or any(not parts for parts in inputs.values()):
        return None
    relations = {port: f"in_{i}" for i, port in enumerate(inputs)}
    queries = inst.sql(relations, in_schemas)
    if queries is None:
        return None
    strict = row_wise(inst)
    checked = inst.sql_checked()
    result = SqlRouteResult({port: 0 for port in out_paths}, relations=relations)
    with _session(inst, inputs, relations, queries, spill_dir, cancel) as session:
        if session is None:
            return None
        con, block_rows = session
        for port, query in queries.items():
            target, schema = out_paths[port], out_schemas.get(port)
            with _duckdb_errors(inst.sql_fallback or strict, inst.name) as failed:
                if target is not None:
                    crs = get_crs(schema) if schema else None
                    result.rows[port] = write_query(con, unguard(query, "check"), target, crs, block_rows)
                else:
                    defined = unguard(query, "define")
                    if strict and not same_types(con.sql(f"SELECT * FROM ({defined}) LIMIT 0").to_arrow_table().schema,
                                                 schema):  # fmt: skip
                        return None
                    names = schema.names if schema is not None else []
                    counts = "".join(f", count({quote(c)})" for c in checked if c in names)
                    found = con.execute(f"SELECT count(*){counts} FROM ({unguard(query, 'check')})").fetchall()
                    result.rows[port] = found[0][0]
                    if define:
                        result.queries[port] = defined
            if failed[0]:
                return None
    return result
