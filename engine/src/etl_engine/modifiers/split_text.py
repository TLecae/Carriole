"""SplitText : découpe un texte selon un séparateur."""

from __future__ import annotations

from typing import Literal

import pyarrow as pa
from pydantic import Field

from etl_engine.data import get_crs, with_crs
from etl_engine.sdk import Attribute, ConfigError, Modifier, Params


class SplitText(Modifier):
    name = "SplitText"
    label = "Découper un texte"
    category = "Attributs"
    description = "Découpe un attribut texte selon un séparateur, en plusieurs attributs ou en une liste."
    help = (
        "« columns » crée `préfixe_1`, `préfixe_2`… (nombre fixé, pour que le schéma soit connu avant "
        "l'exécution) : s'il y a plus de morceaux que de colonnes, le reste est laissé dans la dernière ; s'il y en "
        "a moins, les colonnes en trop sont vides. « list » produit une liste (à éclater avec ExplodeList). "
        "Un texte vide donne des morceaux vides."
    )
    rejected = False

    class Params(Params):
        attribute: Attribute = Field("", title="Attribut à découper", description="Attribut texte à découper.")
        separator: str = Field(
            ",", title="Séparateur", description="Texte exact (pas une expression régulière).", examples=[";"]
        )
        mode: Literal["columns", "list"] = Field(
            "columns",
            title="Résultat",
            description=(
                "columns : une colonne par morceau (préfixe_1, préfixe_2…) ; list : une seule colonne de type "
                "liste, à éclater ensuite avec « Éclater une liste »."
            ),
        )
        prefix: str = Field("", title="Préfixe des attributs créés", description="Vide = nom de l'attribut d'origine.")
        parts: int = Field(
            2,
            ge=1,
            le=100,
            title="Nombre de colonnes (mode columns)",
            description=(
                "Nombre de colonnes créées : le surplus reste dans la dernière, les colonnes en trop sont vides."
            ),
        )
        trim: bool = Field(
            True,
            title="Retirer les espaces autour de chaque morceau",
            description="Retire les espaces au début et à la fin de chaque morceau.",
        )

    def _check(self) -> str:
        p = self.params
        if not p.attribute:
            raise ConfigError("attribut à découper non renseigné")
        if not p.separator:
            raise ConfigError("séparateur vide")
        return p.prefix or p.attribute

    def output_schema(self, inputs):
        prefix = self._check()
        schema = inputs.get("input")
        if schema is None:
            return {"output": None}
        if self.params.attribute not in schema.names:
            raise ConfigError(f"attribut absent : {self.params.attribute}")
        out = schema
        if self.params.mode == "list":
            fields = [pa.field(prefix + "_list" if prefix == self.params.attribute else prefix, pa.list_(pa.string()))]
        else:
            fields = [pa.field(f"{prefix}_{i + 1}", pa.string()) for i in range(self.params.parts)]
        for f in fields:
            out = out.set(out.get_field_index(f.name), f) if f.name in out.names else out.append(f)
        return {"output": out}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        prefix = self._check()
        p = self.params
        if p.attribute not in batch.column_names:
            raise ConfigError(f"attribut absent : {p.attribute}")
        out = batch
        texts = [None if v is None else str(v) for v in batch.column(p.attribute).to_pylist()]
        clean = (lambda s: s.strip()) if p.trim else (lambda s: s)
        if p.mode == "list":
            name = prefix + "_list" if prefix == p.attribute else prefix
            values = [None if t is None else [clean(x) for x in t.split(p.separator)] for t in texts]
            columns = {name: pa.array(values, pa.list_(pa.string()))}
        else:
            columns = {}
            for k in range(p.parts):
                col = []
                for t in texts:
                    if t is None:
                        col.append(None)
                        continue
                    pieces = t.split(p.separator, p.parts - 1)
                    col.append(clean(pieces[k]) if k < len(pieces) else None)
                columns[f"{prefix}_{k + 1}"] = pa.array(col, pa.string())
        for name, column in columns.items():
            if name in out.column_names:
                out = out.set_column(out.column_names.index(name), name, column)
            else:
                out = out.append_column(name, column)
        return {"output": with_crs(out, get_crs(batch))}
