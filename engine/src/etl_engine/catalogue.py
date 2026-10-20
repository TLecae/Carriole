"""Catalogue des modificateurs en Markdown, généré depuis leur déclaration (`etl modifiers doc`)."""

from __future__ import annotations

from typing import Any

from etl_engine import i18n
from etl_engine.registry import Registry


def _fields(schema: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(titre, description, valeur par défaut) de chaque paramètre, dans l'ordre de déclaration."""
    rows = []
    for key, spec in schema.get("properties", {}).items():
        default = spec.get("default")
        rows.append(
            (
                spec.get("title", key),
                spec.get("description", ""),
                ""
                if default in (None, "", [])
                else f"`{default}`"
                if not isinstance(default, bool)
                else str(default).lower(),
            )
        )
    return rows


def render(registry: Registry, lang: str = "fr") -> str:
    def tr(text: str) -> str:
        return i18n.tr(text, lang)

    out = [
        f"# {tr('Catalogue des modificateurs')}",
        "",
        tr("Page générée par `etl modifiers doc` : ne pas la modifier à la main."),
        "",
    ]
    by_category: dict[str, list[dict[str, Any]]] = {}
    for cls in (c for c in registry.all() if not c.hidden):
        by_category.setdefault(cls.describe(lang)["category"], []).append(cls.describe(lang))
    out.append(f"| {tr('Catégorie')} | Modificateurs |")
    out.append("| --- | --- |")
    for category, items in sorted(by_category.items()):
        out.append(f"| {category} | " + ", ".join(f"[{i['label']}](#{i['name'].lower()})" for i in items) + " |")
    out.append("")
    for category, items in sorted(by_category.items()):
        out += [f"## {category}", ""]
        for info in items:
            out += [f"### {info['label']} — `{info['name']}` {{#{info['name'].lower()}}}", "", info["description"], ""]
            ports_in = ", ".join(f"`{p}`" for p in info["inputs"]) or tr("aucune")
            ports_out = ", ".join(f"`{p}`" for p in info["outputs"]) or tr("aucune")
            mode = (
                tr("bloquant (lit toutes les données avant de produire)")
                if info["mode"] == "blocking"
                else tr("au fil de l'eau")
            )
            out += [
                f"- **{tr('Entrées')}** : {ports_in} — **{tr('Sorties')}** : {ports_out} — "
                f"**{tr('Traitement')}** : {mode}",
                "",
            ]
            if info["help"]:
                out += [info["help"], ""]
            rows = _fields(info["params_schema"])
            if rows:
                out += [f"| {tr('Paramètre')} | {tr('Rôle')} | {tr('Défaut')} |", "| --- | --- | --- |"]
                out += [f"| {t} | {d.replace('|', '/')} | {v} |" for t, d, v in rows]
                out.append("")
    return "\n".join(out).rstrip() + "\n"
