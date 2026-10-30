"""ReplaceText : remplacement de texte (littéral ou expression régulière) dans des attributs."""

from __future__ import annotations

import pyarrow as pa
import pyarrow.compute as pc
from pydantic import Field

from etl_engine.data import GEOM
from etl_engine.expr import quote
from etl_engine.sdk import AttributeList, ConfigError, Modifier, Params


class ReplaceText(Modifier):
    name = "ReplaceText"
    label = "Remplacer du texte"
    category = "Attributs"
    description = "Remplace du texte dans des attributs : texte exact ou expression régulière, en place ou dans un nouvel attribut."
    help = (
        "Chaque attribut choisi est converti en texte puis traité. Texte exact : toutes les occurrences de « Chercher » sont "
        "remplacées. Expression régulière (syntaxe RE2) : « Remplacer par » peut citer les groupes (\\1, \\2). « Valeur "
        "entière » remplace seulement les valeurs égales à « Chercher » (par exemple « N/A » par une valeur vide). Avec un "
        "suffixe, le résultat va dans un nouvel attribut `nom` + suffixe et l'original est conservé. Les valeurs vides "
        "restent vides."
    )
    rejected = False

    class Params(Params):
        attributes: AttributeList = Field(default_factory=list, title="Attributs", description="Attributs à traiter.")
        find: str = Field(
            "",
            title="Chercher",
            description="Texte exact, ou expression régulière si « Expression régulière » est coché.",
        )
        replace: str = Field("", title="Remplacer par", description="Vide = supprimer le texte trouvé.")
        regex: bool = Field(
            False, title="Expression régulière", description="« Chercher » est une expression régulière (RE2)."
        )
        whole_value: bool = Field(
            False, title="Valeur entière", description="Remplace seulement les valeurs entières égales à « Chercher »."
        )
        ignore_case: bool = Field(
            False, title="Ignorer la casse", description="Ne distingue pas majuscules et minuscules."
        )
        suffix: str = Field(
            "",
            title="Suffixe du nouvel attribut",
            description="Vide = remplace l'attribut ; sinon crée `nom` + suffixe.",
        )

    def _check(self) -> None:
        p = self.params
        if not p.attributes:
            raise ConfigError("au moins un attribut est nécessaire")
        if not p.find:
            raise ConfigError("le texte à chercher est vide")
        if GEOM in p.attributes:
            raise ConfigError("la géométrie ne se traite pas ici")

    def _target(self, name: str) -> str:
        return name + self.params.suffix

    def output_schema(self, inputs):
        self._check()
        schema = inputs.get("input")
        if schema is None:
            return {"output": None}
        out = schema
        for name in self.params.attributes:
            if name not in schema.names:
                raise ConfigError(f"attribut absent : {name}")
            field = pa.field(self._target(name), pa.string())
            out = out.set(out.get_field_index(field.name), field) if field.name in out.names else out.append(field)
        return {"output": out}

    def sql(self, relations, schemas):
        """Même remplacement par DuckDB (même moteur d'expressions régulières, RE2) ; attributs texte seulement : le
        texte d'un nombre ou d'une date diffère entre Arrow et DuckDB, la voie par lots les traite."""
        self._check()
        p = self.params
        schema = schemas.get("input")
        if schema is None or any(not pa.types.is_string(schema.field(n).type) for n in p.attributes):
            return None
        computed: dict[str, str] = {}
        for name in p.attributes:
            col = quote(name)
            if p.whole_value:
                same = f"lower({col}) = lower({_literal(p.find)})" if p.ignore_case else f"{col} = {_literal(p.find)}"
                expr = f"CASE WHEN {same} THEN {_literal(p.replace)} ELSE {col} END"
            elif p.regex:
                pattern = f"(?i){p.find}" if p.ignore_case else p.find
                expr = f"regexp_replace({col}, {_literal(pattern)}, {_literal(p.replace)}, 'g')"
            elif p.ignore_case:
                pattern = "(?i)" + _escape(p.find)
                expr = f"regexp_replace({col}, {_literal(pattern)}, {_literal(p.replace.replace(chr(92), chr(92) * 2))}, 'g')"
            else:
                expr = f"replace({col}, {_literal(p.find)}, {_literal(p.replace)})"
            computed[self._target(name)] = expr
        kept = [f"{quote(n)}" if n not in computed else f"{computed.pop(n)} AS {quote(n)}" for n in schema.names]
        added = [f"{expr} AS {quote(name)}" for name, expr in computed.items()]
        return {"output": f"SELECT {', '.join([*kept, *added])} FROM {relations['input']}"}

    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        self._check()
        p = self.params
        out = batch
        for name in p.attributes:
            if name not in out.column_names:
                raise ConfigError(f"attribut absent : {name}")
            col = out.column(name)
            if not pa.types.is_string(col.type):
                col = col.cast(pa.string())
            if p.whole_value:
                same = pc.equal(pc.utf8_lower(col), p.find.lower()) if p.ignore_case else pc.equal(col, p.find)
                result = pc.if_else(pc.fill_null(same, False), pa.scalar(p.replace, pa.string()), col)
            else:
                try:
                    if p.regex:
                        pattern = f"(?i){p.find}" if p.ignore_case else p.find
                        result = pc.replace_substring_regex(col, pattern, p.replace)
                    elif p.ignore_case:
                        result = pc.replace_substring_regex(
                            col, "(?i)" + _escape(p.find), p.replace.replace("\\", "\\\\")
                        )
                    else:
                        result = pc.replace_substring(col, p.find, p.replace)
                except pa.ArrowInvalid as exc:
                    raise ConfigError(f"expression régulière invalide : {exc}") from exc
            target = self._target(name)
            out = (
                out.set_column(out.column_names.index(target), target, result)
                if target in out.column_names
                else out.append_column(target, result)
            )
        return {"output": out.replace_schema_metadata(batch.schema.metadata)}


def _escape(text: str) -> str:
    return "".join("\\" + c if c in r".^$*+?()[]{}|\\" else c for c in text)


def _literal(text: str) -> str:
    """Littéral texte SQL."""
    return "'" + text.replace("'", "''") + "'"
