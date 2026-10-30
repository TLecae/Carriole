"""JoinByLocation : compte, additionne, liste les entités liées spatialement à chaque entité, ou en reprend les
attributs (comme une jointure attributaire)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import shapely
from pydantic import Field

from etl_engine.blocking import collect
from etl_engine.data import GEOM, geometries, get_crs, with_crs
from etl_engine.sdk import (
    GEOMETRY_SLICE,
    REJECTED,
    ConfigError,
    Emit,
    Modifier,
    Params,
    attribute_of,
    attributes_of,
    ordered_map,
    reject,
    slices,
)
from etl_engine.spatial import Predicate, SpatialIndex, check_same_crs

RelatedAttribute = attribute_of("related")  # l'éditeur ne propose que les attributs des entités liées
RelatedAttributes = attributes_of("related")

EMIT_ROWS = 100_000  # entités « base » émises par lot (comptes sans liste)
# base <relation> related  <=>  related <inverse> base : pour interroger l'index des entités « base »
INVERSE = {"contains": "within", "within": "contains", "covers": "covered_by", "covered_by": "covers"}


class JoinByLocation(Modifier):
    name = "JoinByLocation"
    label = "Relateur spatial"
    category = "Jointures"
    description = (
        "Ajoute à chaque entité « base » le nombre d'entités « related » en relation avec elle, et au choix la somme "
        "ou la liste de leurs attributs, ou leurs attributs eux-mêmes."
    )
    help = (
        "Pour chaque entité de « base », on compte les entités de « related » qui vérifient la relation "
        "(base <relation> related), 0 s'il n'y en a aucune. Options : additionner des attributs numériques des "
        "entités liées (« <attribut>_sum ») ; lister les valeurs d'un attribut (séparées par un séparateur, sans "
        "doublon, triées) ; récupérer des attributs des entités liées, comme une jointure attributaire (préfixe, "
        "conflit de noms) : ceux de la première entité liée, une entité « base » par entité liée, ou la liste des "
        "valeurs de chaque attribut ; vides sans entité liée. Compter des points dans des polygones : relation "
        "intersects, polygones sur « base », "
        "points sur « related » ; un point sur une limite commune compte dans les deux polygones, ou seulement dans "
        "le premier avec « first ». Sans liste, les entités « related » passent lot par lot sans être gardées en "
        "mémoire : des millions de points se comptent sans peine. Les entités « base » sans géométrie partent sur "
        "« Rejected » ; les entités « related » sans géométrie ne comptent nulle part."
    )
    mode = "blocking"
    inputs = ("base", "related")
    outputs = ("output",)

    class Params(Params):
        predicate: Predicate = Field("intersects", title="Relation", description="base <relation> related.")
        distance: float = Field(
            0.0,
            title="Distance (dwithin)",
            description="Distance maximale pour la relation dwithin, dans l'unité du système de coordonnées.",
        )
        count_attribute: str = Field(
            "_related_count",
            title="Attribut du nombre d'entités liées",
            description="Nom de l'attribut qui reçoit le nombre d'entités de « related » vérifiant la relation.",
        )
        sum_attributes: RelatedAttributes = Field(
            [],
            title="Attributs à additionner",
            description="Attributs numériques de « related » : leur somme par entité « base » dans « <attribut>_sum ».",
        )
        on_multiple: Literal["all", "first"] = Field(
            "all",
            title="Entité liée à plusieurs entités « base »",
            description=(
                "all : comptée pour chacune ; first : pour la première seulement (la somme des comptes égale alors "
                "le nombre d'entités liées)."
            ),
        )
        list_attribute: RelatedAttribute = Field(
            "", title="Lister cet attribut des entités liées", description="Vide = pas de liste."
        )
        list_name: str = Field(
            "_related_values",
            title="Nom de l'attribut de liste",
            description="Nom de l'attribut qui reçoit la liste (si « Lister cet attribut » est renseigné).",
        )
        separator: str = Field(", ", title="Séparateur", description="Texte placé entre deux valeurs de la liste.")
        attributes: RelatedAttributes = Field(
            [],
            title="Attributs à récupérer",
            description="Attributs de « related » ajoutés à l'entité « base », comme une jointure attributaire. Vide = aucun.",
        )
        merge: Literal["first", "all", "list"] = Field(
            "first",
            title="Plusieurs entités liées",
            description=(
                "Pour les attributs récupérés. first : ceux de la première entité liée (ordre de « related ») ; all : "
                "une entité « base » par entité liée ; list : les valeurs distinctes de chaque attribut, jointes par "
                "le séparateur."
            ),
        )
        prefix: str = Field("", title="Préfixe des attributs récupérés", description="Évite les conflits de noms.")
        conflict: Literal["other", "base"] = Field(
            "other",
            title="Si le nom existe déjà",
            description="other : la valeur de « related » ; base : on garde celle de « base ».",
        )

    def _added(self) -> list[tuple[str, pa.DataType]]:
        p = self.params
        added = [(p.count_attribute, pa.int64())] if p.count_attribute else []
        added += [(f"{name}_sum", pa.float64()) for name in p.sum_attributes]
        if p.list_attribute:
            added.append((p.list_name, pa.string()))
        return added

    def _merged(self, base: pa.Schema, related: pa.Schema | None) -> list[tuple[str, str, pa.DataType]]:
        """Attributs récupérés de « related » : (nom en sortie, nom dans « related », type). Un nom déjà pris dans
        « base » est sauté si « base » l'emporte ; `related` inconnu : type nul (aucune entité liée)."""
        p = self.params
        added = {name for name, _ in self._added()}
        merged = []
        for source in p.attributes:
            if source == GEOM:
                raise ConfigError(
                    "la géométrie de « related » ne se récupère pas : celle du résultat est celle de « base »"
                )
            name = p.prefix + source
            if name in added:
                raise ConfigError(f"« {name} » est déjà le nom d'un attribut calculé : choisir un préfixe")
            if p.conflict == "base" and name in base.names:
                continue
            kind = pa.string() if p.merge == "list" else related.field(source).type if related else pa.null()
            merged.append((name, source, kind))
        return merged

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        base, related = inputs.get("base"), inputs.get("related")
        if base is None or (self.params.attributes and related is None):
            return {"output": None}
        if related is not None:
            check_same_crs(base, related)
            self._check_related(related)
        out = base
        for name, kind in [*self._added(), *((n, k) for n, _, k in self._merged(base, related))]:
            field = pa.field(name, kind)
            out = out.set(out.get_field_index(name), field) if name in out.names else out.append(field)
        return {"output": out}

    def _check_related(self, related: pa.Schema) -> None:
        p = self.params
        if GEOM not in related.names:
            raise ConfigError("les entités « related » n'ont pas de géométrie")
        wanted = [*p.sum_attributes, *([p.list_attribute] if p.list_attribute else []), *p.attributes]
        absent = [a for a in wanted if a not in related.names]
        if absent:
            raise ConfigError(f"attributs absents de « related » : {absent}")
        for name in p.sum_attributes:
            kind = related.field(name).type
            if not (pa.types.is_integer(kind) or pa.types.is_floating(kind) or pa.types.is_decimal(kind)):
                raise ConfigError(f"« {name} » n'est pas numérique : impossible de l'additionner")

    def _weights(self, related: pa.Table) -> list[np.ndarray]:
        """Valeurs à additionner de chaque entité liée (vide : 0)."""
        return [
            pc.fill_null(related.column(name).cast(pa.float64()), 0.0).to_numpy(zero_copy_only=False)
            for name in self.params.sum_attributes
        ]

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        # le côté gardé en mémoire et indexé est le plus petit des deux (connu par les fichiers des ports) ; une liste,
        # des attributs récupérés ont besoin des valeurs des entités liées : « related » est alors toujours celui-là
        base_rows, related_rows = _rows(inputs.get("base")), _rows(inputs.get("related"))
        smaller_related = base_rows is not None and related_rows is not None and related_rows < base_rows
        if self.params.list_attribute or self.params.attributes or smaller_related:
            self._index_related(inputs, emit)
        else:
            self._index_base(inputs, emit)

    def _index_base(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        """« base » est lue et indexée une fois ; « related » passe lot par lot, plusieurs lots à la fois, et ne laisse
        que ses comptes et sommes (jamais gardée en mémoire)."""
        p = self.params
        base = collect(inputs.get("base", ()))
        if base is None or base.num_rows == 0:
            for _ in inputs.get("related", ()):  # rien à compléter : les entités liées sont seulement consommées
                pass
            return
        if GEOM not in base.column_names:
            raise ConfigError("les entités « base » n'ont pas de géométrie")
        index = SpatialIndex(base)
        n = base.num_rows
        predicate = INVERSE.get(p.predicate, p.predicate)

        def count(related: pa.Table) -> tuple[np.ndarray, np.ndarray]:
            check_same_crs(base, related)
            self._check_related(related.schema)
            ri, bj = index.pairs(geometries(related), predicate, p.distance)
            if p.on_multiple == "first" and len(ri):
                _, first = np.unique(ri, return_index=True)  # paires triées par entité liée, puis par entité base
                ri, bj = ri[first], bj[first]
            counts = np.bincount(bj, minlength=n).astype(np.int64)
            totals = [np.bincount(bj, weights=w[ri], minlength=n) for w in self._weights(related)]
            return counts, np.array(totals).reshape(len(p.sum_attributes), n)

        counts = np.zeros(n, np.int64)
        totals = np.zeros((len(p.sum_attributes), n))
        for batch_counts, batch_totals in ordered_map(
            count, slices(inputs.get("related", ()), GEOMETRY_SLICE), self.ctx.workers, self.ctx
        ):
            counts += batch_counts
            totals += batch_totals
        out = base
        if p.count_attribute:
            out = _put(out, p.count_attribute, pa.array(counts))
        for name, values in zip(p.sum_attributes, totals, strict=True):
            out = _put(out, f"{name}_sum", pa.array(values, pa.float64()))
        out = with_crs(out, get_crs(base))
        missing = shapely.is_missing(geometries(base))
        kept = out.filter(pa.array(~missing)) if missing.any() else out
        for start in range(0, kept.num_rows, EMIT_ROWS):
            emit("output", kept.slice(start, EMIT_ROWS))
        if missing.any():
            emit(REJECTED, reject(base.filter(pa.array(missing)), "pas de géométrie"))

    def _index_related(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        """« related » est lue et indexée une fois ; « base » passe lot par lot, plusieurs lots à la fois. Les paires
        sont cherchées en parallèle et complétées dans l'ordre : « first » retient la première entité base."""
        p = self.params
        related = collect(inputs.get("related", ()))
        index = values = None
        weights = [np.zeros(0) for _ in p.sum_attributes]
        if related is not None and related.num_rows:
            self._check_related(related.schema)
            index = SpatialIndex(related)
            values = related.column(p.list_attribute) if p.list_attribute else None
            weights = self._weights(related)
        taken = np.zeros(related.num_rows if related is not None else 0, dtype=bool)  # « first » : déjà comptées

        def relate(base: pa.Table) -> tuple[pa.Table, np.ndarray, np.ndarray, np.ndarray]:
            if GEOM not in base.column_names:
                raise ConfigError("les entités « base » n'ont pas de géométrie")
            geoms = geometries(base)
            bi = rj = np.array([], dtype=np.int64)
            if index is not None:
                check_same_crs(base, related)
                bi, rj = index.pairs(geoms, p.predicate, p.distance)
            return base, shapely.is_missing(geoms), bi, rj

        for base, missing, bi, rj in ordered_map(
            relate, slices(inputs.get("base", ()), GEOMETRY_SLICE), self.ctx.workers, self.ctx
        ):
            if p.on_multiple == "first" and len(bi):
                fresh = np.flatnonzero(~taken[rj])
                _, first = np.unique(rj[fresh], return_index=True)  # première entité base de chaque entité liée
                keep = np.sort(fresh[first])
                bi, rj = bi[keep], rj[keep]
                taken[rj] = True
            n = base.num_rows
            out = base
            if p.count_attribute:
                out = _put(out, p.count_attribute, pa.array(np.bincount(bi, minlength=n).astype(np.int64)))
            for name, w in zip(p.sum_attributes, weights, strict=True):
                out = _put(out, f"{name}_sum", pa.array(np.bincount(bi, weights=w[rj], minlength=n), pa.float64()))
            if p.list_attribute:
                out = _put(out, p.list_name, _lists(n, bi, rj, values, p.separator))
            keep = ~missing
            if p.attributes:
                out, keep = self._merge(out, base, related, bi, rj, keep)
            emit("output", with_crs(out, get_crs(base)).filter(pa.array(keep)))
            if missing.any():
                emit(REJECTED, reject(base.filter(pa.array(missing)), "pas de géométrie"))

    def _merge(
        self, out: pa.Table, base: pa.Table, related: pa.Table | None, bi: np.ndarray, rj: np.ndarray, keep: np.ndarray
    ) -> tuple[pa.Table, np.ndarray]:
        """Attributs récupérés des entités liées (paires `bi`, `rj` triées par entité base puis liée) ; « all » : une
        ligne par paire, et une, sans attributs, pour l'entité base qui n'en a pas (ordre de « base » conservé)."""
        p = self.params
        n = out.num_rows
        merged = self._merged(base.schema, related.schema if related is not None and related.num_rows else None)
        if p.merge == "list":
            for name, source, _ in merged:
                values = related.column(source) if related is not None and related.num_rows else None
                out = _put(out, name, _lists(n, bi, rj, values, p.separator))
            return out, keep
        if p.merge == "all":
            alone = np.setdiff1d(np.arange(n), bi)  # sans entité liée
            rows = np.concatenate([bi, alone])
            picks = np.concatenate([rj, np.full(len(alone), -1, np.int64)])
            order = np.argsort(rows, kind="stable")  # les paires restent dans l'ordre des entités liées
            rows, picks = rows[order], picks[order]
            out, keep = out.take(pa.array(rows)), keep[rows]
        else:  # first : la première entité liée de chaque entité base
            picks = np.full(n, -1, np.int64)
            found, first = np.unique(bi, return_index=True)
            picks[found] = rj[first]
        index = pa.array(picks, mask=picks < 0)
        for name, source, kind in merged:
            column = related.column(source).take(index) if kind != pa.null() else pa.nulls(len(picks))
            out = _put(out, name, column)
        return out, keep


def _rows(source: Any) -> int | None:
    """Nombre d'entités d'une entrée, s'il est connu d'avance (`PortSource.total`), None sinon."""
    return getattr(source, "total", None)


def _lists(n: int, bi: np.ndarray, rj: np.ndarray, values: pa.ChunkedArray | None, separator: str) -> pa.Array:
    """Pour chaque entité base : valeurs distinctes non nulles des entités liées, triées et jointes (None si aucune)."""
    texts: list[str | None] = [None] * n
    if values is None or not len(bi):
        return pa.array(texts, pa.string())
    taken = values.take(pa.array(rj, pa.int64())).to_pylist()
    bounds = np.flatnonzero(np.diff(bi)) + 1  # bi est trié : une tranche par entité base
    for start, stop in zip(np.r_[0, bounds], np.r_[bounds, len(bi)], strict=True):
        found = {str(v) for v in taken[start:stop] if v is not None}
        if found:
            texts[int(bi[start])] = separator.join(sorted(found))
    return pa.array(texts, pa.string())


def _put(table: pa.Table, name: str, column: pa.Array) -> pa.Table:
    if name in table.column_names:
        return table.set_column(table.column_names.index(name), name, column)
    return table.append_column(name, column)
