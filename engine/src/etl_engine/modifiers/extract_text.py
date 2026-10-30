"""ExtractText : extrait une partie d'un texte (position, séparateur, expression régulière)."""

from __future__ import annotations

import re
from typing import Literal

import pyarrow as pa
import pyarrow.compute as pc
from pydantic import Field

from etl_engine.modifiers.split_text import pieces
from etl_engine.sdk import Attribute, ConfigError, Modifier, Params


class ExtractText(Modifier):
    name = "ExtractText"
    label = "Extraire du texte"
    category = "Attributs"
    description = "Extrait une partie d'un attribut texte : sous-chaîne, avant/après un séparateur, ou captures d'une expression régulière."
    help = (
        "« substring » : à partir de la position « Début » (1 = premier caractère), sur « Longueur » caractères (vide = "
        "jusqu'à la fin) ; un début négatif compte depuis la fin. « before » / « after » : ce qui précède / suit la première "
        "occurrence du séparateur (vide si absent). « regex » : une expression régulière avec des groupes **nommés** "
        "`(?P<code>\\d{5})` ; chaque groupe donne un attribut (préfixé par « Préfixe »), vide si le texte ne correspond "
        "pas. Syntaxe RE2. Exemple : `(?P<dep>\\d{2})(?P<com>\\d{3})` sur un code INSEE."
    )
    rejected = False

    class Params(Params):
        attribute: Attribute = Field("", title="Attribut", description="Attribut texte à analyser.")
        mode: Literal["substring", "before", "after", "regex"] = Field(
            "substring", title="Extraction", description="substring, before, after ou regex."
        )
        start: int = Field(
            1,
            title="Début (substring)",
            description="Position du premier caractère (1 = premier ; négatif depuis la fin).",
        )
        length: int | None = Field(
            None, ge=0, title="Longueur (substring)", description="Nombre de caractères ; vide = jusqu'à la fin."
        )
        separator: str = Field(
            "", title="Séparateur (before / after)", description="Texte exact (pas une expression régulière)."
        )
        pattern: str = Field(
            "", title="Expression (regex)", description="Expression régulière RE2 avec des groupes nommés (?P<nom>…)."
        )
        destination: str = Field(
            "",
            title="Attribut créé",
            description="substring / before / after : nom du résultat (vide = `attribut` + _extrait).",
        )
        prefix: str = Field("", title="Préfixe (regex)", description="Ajouté devant le nom de chaque groupe.")

    def _groups(self) -> list[str]:
        try:
            compiled = re.compile(self.params.pattern)
        except re.error as exc:
            raise ConfigError(f"expression régulière invalide : {exc}") from exc
        names = [n for n, _ in sorted(compiled.groupindex.items(), key=lambda kv: kv[1])]
        if not names:
            raise ConfigError("l'expression doit contenir au moins un groupe nommé (?P<nom>…)")
        return names

    def _check(self) -> list[str]:
        p = self.params
        if not p.attribute:
            raise ConfigError("attribut non renseigné")
        if p.mode == "regex":
            return [p.prefix + g for g in self._groups()]
        if p.mode in ("before", "after") and not p.separator:
            raise ConfigError("séparateur vide")
        return [p.destination or f"{p.attribute}_extrait"]

    def output_schema(self, inputs):
        names = self._check()
        schema = inputs.get("input")
        if schema is None:
            return {"output": None}
        if self.params.attribute not in schema.names:
            raise ConfigError(f"attribut absent : {self.params.attribute}")
        out = schema
        for n in names:
            field = pa.field(n, pa.string())
            out = out.set(out.get_field_index(n), field) if n in out.names else out.append(field)
        return {"output": out}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        p = self.params
        names = self._check()
        if p.attribute not in batch.column_names:
            raise ConfigError(f"attribut absent : {p.attribute}")
        col = batch.column(p.attribute)
        if not pa.types.is_string(col.type):
            col = col.cast(pa.string())
        results: list[pa.ChunkedArray | pa.Array]
        if p.mode == "substring":
            start = p.start - 1 if p.start > 0 else p.start
            stop = (
                None
                if p.length is None
                else (start + p.length if start >= 0 else (start + p.length if start + p.length < 0 else None))
            )
            results = [pc.utf8_slice_codeunits(col, start, stop)]
        elif p.mode in ("before", "after"):
            before, after = pieces(col.combine_chunks() if isinstance(col, pa.ChunkedArray) else col, p.separator, 2)
            found = pc.is_valid(after)  # sans séparateur : rien avant, rien après
            results = [pc.if_else(found, before, pa.scalar(None, pa.string())) if p.mode == "before" else after]
        else:
            try:
                found = pc.extract_regex(col, p.pattern)
            except pa.ArrowInvalid as exc:
                raise ConfigError(f"expression régulière invalide : {exc}") from exc
            results = [pc.struct_field(found, g) for g in self._groups()]
        out = batch
        for name, result in zip(names, results, strict=True):
            out = (
                out.set_column(out.column_names.index(name), name, result)
                if name in out.column_names
                else out.append_column(name, result)
            )
        return {"output": out.replace_schema_metadata(batch.schema.metadata)}
