"""CreateFeatures : crée des entités de toutes pièces (une ou plusieurs), avec ou sans géométrie."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import numpy as np
import pyarrow as pa
import shapely
from pydantic import Field

from etl_engine.data import GEOM, with_crs
from etl_engine.expr import evaluate
from etl_engine.sdk import ConfigError, Crs, Emit, Expression, Modifier, Params


class CreatedAttribute(Params):
    name: str = Field("", title="Nom de l'attribut", description="Nom de l'attribut créé.")
    expression: Expression = Field(
        "",
        title="Expression",
        description="Expression SQL, ex. 42, 'texte' ou \"_index\" * 10 (_index : numéro de l'entité, de 0).",
    )


class CreateFeatures(Modifier):
    name = "CreateFeatures"
    label = "Créer des entités"
    category = "Entrées / sorties"
    description = "Crée une ou plusieurs entités de toutes pièces : attributs calculés et géométrie facultative."
    help = (
        "Point de départ d'un flux qui n'a pas de fichier à lire : une seule entité pour déclencher des nœuds en aval "
        "(DatabaseQuery, HttpRequest, RunFlow…) ou plusieurs entités de test. Chaque attribut est une expression SQL "
        "qui peut utiliser `_index` (0, 1, 2…) ; les expressions suivantes voient les précédentes. Géométrie : « none », "
        "« point » (X et Y) ou « wkt » (texte WKT, ex. POLYGON((0 0, 1 0, 1 1, 0 0))). Le système de coordonnées s'applique "
        "à la géométrie."
    )
    inputs = ()
    rejected = False

    class Params(Params):
        count: int = Field(1, ge=1, le=1_000_000, title="Nombre d'entités", description="Combien d'entités créer.")
        attributes: list[CreatedAttribute] = Field(
            default_factory=list, title="Attributs", description="Calculés dans l'ordre, pour chaque entité."
        )
        geometry: Literal["none", "point", "wkt"] = Field("none", title="Géométrie", description="none, point ou wkt.")
        x: Expression = Field("0", title="X (point)", description='Expression SQL de X, ex. 1.5 ou 600000 + "_index".')
        y: Expression = Field("0", title="Y (point)", description="Expression SQL de Y.")
        wkt: str = Field("", title="WKT", description="Géométrie WKT, identique pour toutes les entités.")
        crs: Crs = Field("", title="Système de coordonnées", description="Celui de la géométrie.")

    def _check(self) -> None:
        names = [a.name for a in self.params.attributes]
        if any(not a.name or not a.expression.strip() for a in self.params.attributes):
            raise ConfigError("chaque attribut demande un nom et une expression")
        if len(set(names)) != len(names):
            raise ConfigError("attribut en double")
        if GEOM in names or "_index" in names:
            raise ConfigError("nom d'attribut réservé")
        if self.params.geometry == "wkt":
            try:
                shapely.from_wkt(self.params.wkt)
            except Exception as exc:  # noqa: BLE001 - shapely lève plusieurs types selon la version
                raise ConfigError(f"WKT invalide : {exc}") from exc

    def _build(self, start: int, n: int) -> pa.Table:
        """Les entités numérotées de `start` à `start + n - 1` (`_index` est le numéro global)."""
        p = self.params
        index = pa.table({"_index": pa.array(np.arange(start, start + n), pa.int64())})
        table = index
        for a in p.attributes:
            table = table.append_column(a.name, evaluate(table, {a.name: a.expression}).column(a.name))
        table = table.drop_columns(["_index"])
        if p.geometry == "point":
            xs = evaluate(index, {"v": p.x}).column("v").to_numpy(zero_copy_only=False).astype(float)
            ys = evaluate(index, {"v": p.y}).column("v").to_numpy(zero_copy_only=False).astype(float)
            column = pa.array(shapely.to_wkb(shapely.points(xs, ys)), pa.binary())
        elif p.geometry == "wkt":  # la même géométrie pour toutes : encodée une fois
            column = pa.repeat(pa.scalar(shapely.to_wkb(shapely.from_wkt(p.wkt)), pa.binary()), n)
        else:
            return table
        table = table.append_column(GEOM, column)
        return with_crs(table, p.crs or None)

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        self._check()
        return {"output": self._build(0, 1).schema}

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        self._check()
        total, done, step = self.params.count, 0, max(1, self.ctx.batch_rows)
        while done < total:
            self.ctx.check_cancel()
            n = min(step, total - done)
            emit("output", self._build(done, n))
            done += n
        self.ctx.info(f"{total} entité(s) créée(s)")
