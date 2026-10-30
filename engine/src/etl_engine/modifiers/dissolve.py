"""Dissolve : fusionne les géométries d'un même groupe."""

from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import shapely
from pydantic import Field

from etl_engine.blocking import collect, group_codes, group_slices
from etl_engine.data import GEOM, get_crs, with_crs
from etl_engine.sdk import AttributeList, ConfigError, Emit, Modifier, Params, missing_attributes, require_attributes
from etl_engine.spatial import zorder_bounds


class Dissolve(Modifier):
    name = "Dissolve"
    label = "Fusionner (dissoudre)"
    category = "Géométrie"
    description = (
        "Fusionne les polygones (ou lignes) de chaque groupe en une seule géométrie, sans frontières internes."
    )
    help = (
        "Une entité par groupe, avec les attributs de la première entité du groupe. Les polygones qui se touchent "
        "sont fusionnés ; les lignes sont réunies puis jointes bout à bout quand c'est possible. Les entités sans "
        "géométrie sont ignorées du calcul. Différent d'Aggregate, qui réunit sans fusionner."
    )
    mode = "blocking"
    outputs = ("output",)
    rejected = False

    class Params(Params):
        group_by: AttributeList = Field([], title="Regrouper par", description="Vide = tout fusionner.")
        count_attribute: str = Field(
            "_count", title="Attribut du nombre d'entités", description="Vide = pas de comptage."
        )

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        schema = inputs.get("input")
        if schema is not None and GEOM not in schema.names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        require_attributes(schema, self.params.group_by)
        if schema is None or not self.params.count_attribute or self.params.count_attribute in schema.names:
            return {"output": schema}
        return {"output": schema.append(pa.field(self.params.count_attribute, pa.int64()))}

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        """Les géométries restent en WKB (compact) : elles ne sont décodées que par morceaux de `CHUNK`, au moment de
        les fusionner, quelques morceaux à la fois. Décoder tout un port d'un coup coûtait ~10 fois sa taille en
        mémoire (1,47 M de parcelles : 6 Go pour un résultat de 600 Mo)."""
        p = self.params
        table = collect(inputs.get("input", ()))
        if table is None or table.num_rows == 0:
            return
        if GEOM not in table.column_names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        missing = [g for g in p.group_by if g not in table.column_names]
        if missing:
            raise missing_attributes(missing)
        crs = get_crs(table)
        has_geom = ~pc.is_null(table.column(GEOM)).to_numpy(zero_copy_only=False)
        # une seule colonne contiguë : `take` sur une colonne en morceaux la recopierait en entier à chaque appel
        wkb = pa.concat_arrays(table.column(GEOM).cast(pa.large_binary()).chunks)
        codes, count = group_codes(table, list(p.group_by))
        order, bounds = group_slices(codes, count)
        total = table.num_rows
        # la sortie ne garde que les attributs de la première entité de chaque groupe : le reste du port est libéré
        firsts = table.take(pa.array(order[bounds[:-1]], pa.int64()))
        del table, codes
        with ThreadPoolExecutor(max_workers=max(1, self.ctx.workers)) as pool:
            start = 0
            while start < count:  # par paquets de groupes (~ROWS_PER_PACKET lignes) : les résultats ne s'accumulent pas
                self.ctx.check_cancel()
                stop = start + 1
                while (
                    stop < count and stop - start < GROUPS_PER_EMIT
                    and bounds[stop + 1] - bounds[start] <= ROWS_PER_PACKET
                ):  # fmt: skip
                    stop += 1
                members = []
                for g in range(start, stop):
                    rows = order[bounds[g] : bounds[g + 1]]
                    members.append(rows[has_geom[rows]])
                merged = _union_groups(wkb, members, pool)
                out = firsts.slice(start, stop - start)
                out = out.set_column(out.column_names.index(GEOM), GEOM, pa.array(merged, pa.binary()))
                if p.count_attribute:
                    counts = pa.array(np.diff(bounds[start : stop + 1]), pa.int64())
                    if p.count_attribute in out.column_names:
                        out = out.set_column(out.column_names.index(p.count_attribute), p.count_attribute, counts)
                    else:
                        out = out.append_column(p.count_attribute, counts)
                emit("output", with_crs(out, crs))
                start = stop
        self.ctx.info(f"{total} entités fusionnées en {count}")


GROUPS_PER_EMIT = 5_000
ROWS_PER_PACKET = 600_000  # lignes d'un paquet de groupes (un groupe plus gros forme son propre paquet)
CHUNK = 10_000  # au-delà, un groupe est fusionné par morceaux voisins, en parallèle, puis les morceaux entre eux
BOUNDS_ROWS = 50_000  # lignes décodées à la fois pour en lire l'emprise (ordre de Morton des gros groupes)


def _finish(union: Any) -> bytes | None:
    if union is not None and union.geom_type in ("MultiLineString", "LineString"):
        union = shapely.line_merge(union)
    return None if union is None or union.is_empty else shapely.to_wkb(union)


def _decode(wkb: pa.Array, rows: np.ndarray) -> np.ndarray:
    """Géométries des lignes `rows` (décodées ici, et nulle part ailleurs)."""
    return shapely.from_wkb(wkb.take(pa.array(rows, pa.int64())).to_numpy(zero_copy_only=False))


def _union_groups(wkb: pa.Array, members: list[np.ndarray], pool: ThreadPoolExecutor) -> list[bytes | None]:
    """Union de chaque groupe (`members` : ses lignes ayant une géométrie). Les groupes sont indépendants : répartis
    entre threads (GEOS libère le GIL). Un gros groupe est d'abord coupé en morceaux voisins (ordre de Morton)
    décodés et fusionnés en parallèle, puis réunis. Les petits groupes sont rassemblés en une seule tâche.

    Union « par sous-ensembles disjoints » : chaque amas de géométries qui se touchent est fusionné à part, les amas
    isolés ne sont jamais recalculés ensemble (parcelles d'une culture dispersées sur un département : 0,6 s au lieu
    de 55 s pour 60 000). Pour un ensemble d'un seul tenant, c'est l'union ordinaire."""
    tasks: list[tuple[str, Any]] = []
    plan: list[tuple[int, int]] = []  # pour chaque groupe : (première tâche, nombre de tâches ; 0 : dans un lot)
    small: list[int] = []  # petits groupes en attente d'un lot
    pending = [0]  # entités des petits groupes en attente

    def flush() -> None:
        if small:
            tasks.append(("small", [(g, members[g]) for g in small]))
            for g in small:
                plan[g] = (len(tasks) - 1, 0)
            small.clear()
            pending[0] = 0

    for g, rows in enumerate(members):
        plan.append((-1, 1))
        if len(rows) > CHUNK:
            flush()
            rows = rows[_morton(wkb, rows, pool)]
            first = len(tasks)
            tasks.extend(("piece", rows[i : i + CHUNK]) for i in range(0, len(rows), CHUNK))
            plan[g] = (first, len(tasks) - first)
        else:
            small.append(g)
            pending[0] += len(rows)
            if pending[0] >= CHUNK:
                flush()
    flush()

    def run(task: tuple[str, Any]) -> Any:
        kind, payload = task
        if kind == "piece":
            return shapely.disjoint_subset_union_all(_decode(wkb, payload)) if len(payload) else None
        return {g: _small_union(wkb, rows) for g, rows in payload}

    done = list(pool.map(run, tasks))

    def merge(g: int) -> bytes | None:
        first, n = plan[g]
        if n == 0:
            return _finish(done[first][g])
        if n == 1:
            return _finish(done[first])
        return _finish(shapely.disjoint_subset_union_all(np.array(done[first : first + n], dtype=object)))

    return list(pool.map(merge, range(len(members))))


def _small_union(wkb: pa.Array, rows: np.ndarray) -> Any:
    if not len(rows):
        return None
    geoms = _decode(wkb, rows)
    return geoms[0] if len(geoms) == 1 else shapely.disjoint_subset_union_all(geoms)


def _morton(wkb: pa.Array, rows: np.ndarray, pool: ThreadPoolExecutor) -> np.ndarray:
    """Ordre de Morton des lignes d'un gros groupe : leurs emprises sont lues par tranches, sans garder les
    géométries décodées."""
    parts = [rows[i : i + BOUNDS_ROWS] for i in range(0, len(rows), BOUNDS_ROWS)]
    boxes = list(pool.map(lambda part: shapely.bounds(_decode(wkb, part)), parts))
    return zorder_bounds(np.concatenate(boxes))
