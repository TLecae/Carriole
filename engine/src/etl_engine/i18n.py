"""Internationalisation du moteur : le français est la langue source du code, les autres langues sont des catalogues.

- **Descriptions** (libellé, aide, titres de paramètres) : clés stables `<Modificateur>.label`,
  `<Modificateur>.params.<champ>.title`… dans `locales/<langue>.json`, section `modifiers`. Une clé absente
  retombe sur le texte français du code.
- **Messages** (erreurs de configuration, avertissements d'analyse, journal des runs, erreurs d'API) : le texte
  français sert de clé, avec des `{noms}` à la place des valeurs ; la traduction garde les mêmes noms. Un message déjà
  formé (« attribut absent en amont : nom ») est reconnu par son gabarit ; les valeurs qu'il contient sont traduites à
  leur tour, ce qui couvre les messages composés (« nœud : configuration : … »).
- **Plugins** : un fichier `<plugin>.locales.json` à côté du `.py`, de forme
  `{"en": {"modifiers": {…}, "messages": {…}}}`.

Ajouter une langue = ajouter `locales/<code>.json` (voir `etl i18n check`).
"""

from __future__ import annotations

import json
import re
import threading
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

SOURCE_LANGUAGE = "fr"
LOCALES_DIR = Path(__file__).parent / "locales"

_lock = threading.Lock()
_extra_files: list[Path] = []  # catalogues de plugins


def normalize(lang: str | None) -> str:
    """`en-US,en;q=0.9` -> `en` ; vide -> langue du réglage."""
    if not lang:
        from etl_engine import settings

        return settings.language()
    return lang.split(",")[0].split(";")[0].strip().replace("_", "-").lower().split("-")[0] or SOURCE_LANGUAGE


LANGUAGE_NAMES = {"fr": "Français", "en": "English"}


def language_name(code: str) -> str:
    """Nom d'une langue dans cette langue ; un catalogue peut le déclarer sous `meta.name`."""
    declared = _read(LOCALES_DIR / f"{code}.json").get("meta", {})
    return str(declared.get("name") or LANGUAGE_NAMES.get(code) or code)


def available_languages() -> list[str]:
    found = {p.stem for p in LOCALES_DIR.glob("*.json")} if LOCALES_DIR.is_dir() else set()
    return [SOURCE_LANGUAGE, *sorted(found - {SOURCE_LANGUAGE})]


def add_plugin_catalog(path: str | Path) -> None:
    """Déclare le catalogue d'un plugin (`<plugin>.locales.json`)."""
    path = Path(path)
    with _lock:
        if path not in _extra_files:
            _extra_files.append(path)
    _catalog.cache_clear()


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=16)
def _catalog(lang: str) -> dict[str, dict[str, str]]:
    merged: dict[str, dict[str, str]] = {"modifiers": {}, "messages": {}}
    sources: list[dict[str, Any]] = [_read(LOCALES_DIR / f"{lang}.json")]
    for extra in list(_extra_files):
        sources.append(_read(extra).get(lang, {}))
    for source in sources:
        if "transformers" in source and "modifiers" not in source:  # catalogue de plugin écrit avant le renommage
            source = {**source, "modifiers": source["transformers"]}
        for section in merged:
            part = source.get(section)
            if isinstance(part, dict):
                merged[section].update({k: v for k, v in part.items() if isinstance(v, str)})
    return merged


def catalog(lang: str | None, section: str = "modifiers") -> dict[str, str]:
    return _catalog(normalize(lang))[section]


def reload() -> None:
    _catalog.cache_clear()
    _templates.cache_clear()


# --- descriptions des modificateurs ---------------------------------------------------------------------------


def localize_description(name: str, description: dict[str, Any], lang: str | None) -> dict[str, Any]:
    """Applique le catalogue à la description d'un modificateur (`Modifier.describe`). Copie, jamais en place."""
    lang = normalize(lang)
    if lang == SOURCE_LANGUAGE:
        return description
    texts = catalog(lang)
    out = deepcopy(description)
    for key in ("label", "category", "description", "help"):
        if key == "category":
            out[key] = texts.get(f"category.{description[key]}", description[key])
        else:
            out[key] = texts.get(f"{name}.{key}", description[key])
    schema = out.get("params_schema") or {}
    _localize_properties(schema.get("properties"), f"{name}.params", texts)
    for def_name, sub in (schema.get("$defs") or {}).items():
        _localize_properties(sub.get("properties"), f"{name}.defs.{def_name}", texts)
    return out


def _localize_properties(props: dict[str, Any] | None, prefix: str, texts: dict[str, str]) -> None:
    for field, spec in (props or {}).items():
        for key in ("title", "description"):
            if key in spec:
                spec[key] = texts.get(f"{prefix}.{field}.{key}", spec[key])


def localize_category(category: str, lang: str | None) -> str:
    """Nom d'une catégorie dans la langue (celles rédigées par l'utilisateur restent telles quelles)."""
    lang = normalize(lang)
    return category if lang == SOURCE_LANGUAGE else catalog(lang).get(f"category.{category}", category)


def description_texts(name: str, description: dict[str, Any]) -> dict[str, str]:
    """Textes traduisibles d'une description, par clé stable (sert à `etl i18n extract` et `check`)."""
    out: dict[str, str] = {}
    for key in ("label", "description", "help"):
        if description.get(key):
            out[f"{name}.{key}"] = description[key]
    if description.get("category"):
        out[f"category.{description['category']}"] = description["category"]
    schema = description.get("params_schema") or {}

    def collect(props: dict[str, Any] | None, prefix: str) -> None:
        for field, spec in (props or {}).items():
            for key in ("title", "description"):
                text = spec.get(key)
                if not text:
                    continue
                if key == "title" and text == field.replace("_", " ").title():
                    continue  # titre déduit du nom du champ par Pydantic : pas un texte rédigé
                out[f"{prefix}.{field}.{key}"] = text

    collect(schema.get("properties"), f"{name}.params")
    for def_name, sub in (schema.get("$defs") or {}).items():
        collect(sub.get("properties"), f"{name}.defs.{def_name}")
    return out


# --- messages -------------------------------------------------------------------------------------------------

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _compile(template: str) -> re.Pattern[str]:
    pieces: list[str] = []
    last = 0
    for match in _PLACEHOLDER.finditer(template):
        pieces.append(re.escape(template[last : match.start()]))
        pieces.append(f"(?P<{match.group(1)}>.+?)")
        last = match.end()
    pieces.append(re.escape(template[last:]))
    return re.compile("^" + "".join(pieces) + "$", re.DOTALL)


@lru_cache(maxsize=16)
def _templates(lang: str) -> tuple[tuple[re.Pattern[str], str], ...]:
    messages = _catalog(lang)["messages"]
    pairs = [(_compile(fr), en) for fr, en in messages.items() if _PLACEHOLDER.search(fr)]
    # les gabarits les plus spécifiques (plus de texte fixe) d'abord
    pairs.sort(key=lambda p: -len(p[0].pattern))
    return tuple(pairs)


def tr(text: str, lang: str | None = None) -> str:
    """Traduit un message français (formé ou non) ; renvoie le texte tel quel s'il est inconnu."""
    if not text:
        return text
    lang = normalize(lang)
    if lang == SOURCE_LANGUAGE:
        return text
    return _translate(text, lang)


def _translate(text: str, lang: str) -> str:
    messages = _catalog(lang)["messages"]
    if text in messages:
        return messages[text]
    for pattern, target in _templates(lang):
        match = pattern.match(text)
        if match:
            values = {k: _translate(v, lang) for k, v in match.groupdict().items()}
            try:
                return target.format(**values)
            except (KeyError, IndexError, ValueError):
                return target
    for sep in ("\n", "; ", " : "):  # message composé : chaque morceau est traduit
        if sep in text:
            parts = text.split(sep)
            translated = [_translate(p, lang) for p in parts]
            if translated != parts:
                return (": " if sep == " : " else sep).join(translated)  # la typographie française (« : ») s'efface
    return text


def t(template: str, lang: str | None = None, **values: Any) -> str:
    """Message neuf : `t("fichier introuvable : {path}", path=p)`. Le gabarit français est la clé."""
    lang = normalize(lang)
    target = template if lang == SOURCE_LANGUAGE else catalog(lang, "messages").get(template, template)
    try:
        return target.format(**values)
    except (KeyError, IndexError, ValueError):
        return template.format(**values) if values else template


def translate_json(value: Any, lang: str | None, keys: frozenset[str]) -> Any:
    """Traduit en place les chaînes des clés `keys` d'un JSON imbriqué (réponses d'analyse, événements)."""
    lang = normalize(lang)
    if lang == SOURCE_LANGUAGE:
        return value
    if isinstance(value, dict):
        for k, v in value.items():
            if k in keys and isinstance(v, str):
                value[k] = _translate(v, lang)
            else:
                translate_json(v, lang, keys)
    elif isinstance(value, list):
        for item in value:
            translate_json(item, lang, keys)
    return value
