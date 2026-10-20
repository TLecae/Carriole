"""Outillage des catalogues de langue : extraction des textes français du code, contrôle des clés manquantes.

Utilisé par `etl i18n extract` et `etl i18n check`, et par les tests (aucun texte ne reste sans traduction).
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from etl_engine import i18n

# Appels dont les arguments texte sont montrés à l'utilisateur.
MESSAGE_CALLS = {
    "ConfigError",
    "ProviderError",
    "ValueError",
    "MemoryError",
    "RuntimeError",
    "FileNotFoundError",
    "PermissionError",
    "NotImplementedError",
    "OSError",
    "KeyError",
    "HTTPException",
    "DataError",
    "Issue",
    "CliError",
    "say",
    "tr",
    "BadParameter",
    "BackendError",
    "info",
    "warning",
    "error",
    "log",
    "event",
}
_TEXT = re.compile(r"[A-Za-zÀ-ÿ]{3,}")
_NAME = re.compile(r"[^A-Za-z0-9_]+")


SINGLE_WORD_CALLS = {"tr", "say"}  # ces appels n'ont que du texte à traduire : un mot seul compte


def _worth_translating(text: str, single_word: bool = False) -> bool:
    """Un vrai message : du texte fixe (pas seulement des valeurs et de la ponctuation), pas un format de journal."""
    fixed = re.sub(r"\{[A-Za-z_][A-Za-z0-9_]*\}", "", text)
    return len(re.findall(r"[A-Za-zÀ-ÿ]", fixed)) >= 3 and (single_word or " " in text.strip()) and "%" not in text


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _placeholder(expr: ast.expr, used: set[str]) -> str:
    base = "v"
    if isinstance(expr, ast.Name):
        base = expr.id
    elif isinstance(expr, ast.Attribute):
        base = expr.attr
    elif isinstance(expr, ast.Subscript) and isinstance(expr.value, ast.Name):
        base = expr.value.id
    elif isinstance(expr, ast.Call):
        base = _call_name(expr) or "v"
    base = _NAME.sub("_", base).strip("_") or "v"
    if base[0].isdigit():
        base = "v" + base
    name, n = base, 2
    while name in used:
        name, n = f"{base}{n}", n + 1
    used.add(name)
    return name


def template_of(node: ast.expr) -> str | None:
    """Texte d'une chaîne ou d'une f-string, valeurs remplacées par `{nom}` ; `None` si ce n'est pas un texte."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        used: set[str] = set()
        out: list[str] = []
        for part in node.values:
            if isinstance(part, ast.Constant):
                out.append(str(part.value))
            elif isinstance(part, ast.FormattedValue):
                out.append("{" + _placeholder(part.value, used) + "}")
        return "".join(out)
    return None


def _branches(node: ast.expr) -> list[ast.expr]:
    """Les textes possibles d'une expression : les deux branches d'un `a if c else b`."""
    if isinstance(node, ast.IfExp):
        return [*_branches(node.body), *_branches(node.orelse)]
    if isinstance(node, ast.Dict):  # detail={"message": "…", …}
        return [
            v
            for k, v in zip(node.keys, node.values, strict=True)
            if isinstance(k, ast.Constant) and k.value == "message"
        ]
    return [node]


def source_messages(roots: list[Path]) -> dict[str, list[str]]:
    """Gabarits de messages trouvés dans les sources Python : {gabarit: [fichier:ligne, …]}."""
    found: dict[str, list[str]] = {}
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if "tests" in path.parts or "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or _call_name(node) not in MESSAGE_CALLS:
                    continue
                args = [*node.args, *(k.value for k in node.keywords if k.arg in {"detail", "message", "error"})]
                single = _call_name(node) in SINGLE_WORD_CALLS
                for arg in args:
                    for branch in _branches(arg):
                        for line in (template_of(branch) or "").split(chr(10)):  # plusieurs lignes : une clé chacune
                            if _worth_translating(line, single):
                                found.setdefault(line, []).append(f"{path.name}:{node.lineno}")
    return found


def modifier_texts() -> dict[str, str]:
    """Tous les textes de description des modificateurs intégrés, par clé stable."""
    from etl_engine.registry import Registry

    registry = Registry()
    registry.load_builtin()
    out: dict[str, str] = {}
    for cls in registry.all():
        out.update(i18n.description_texts(cls.name, cls._describe_source()))
    return out


def project_roots() -> list[Path]:
    """Dossiers de sources du moteur, de l'API, du worker et du magasin (arborescence de développement)."""
    here = Path(__file__).resolve()
    repo = here.parents[3] if (here.parents[3] / "api").is_dir() else None
    roots = [here.parent]
    if repo:
        roots += [repo / "api" / "src", repo / "worker" / "src", repo / "store" / "src", repo / "assistant" / "src"]
    return [r for r in roots if r.is_dir()]


def check(lang: str) -> dict[str, Any]:
    """Écarts d'un catalogue : clés manquantes (à traduire) et clés orphelines (le code a changé)."""
    texts = modifier_texts()
    messages = source_messages(project_roots())
    cat_t = i18n.catalog(lang, "modifiers")
    cat_m = i18n.catalog(lang, "messages")
    return {
        "missing_modifiers": sorted(k for k in texts if k not in cat_t),
        "orphan_modifiers": sorted(k for k in cat_t if k not in texts and not k.startswith("category.")),
        "missing_messages": sorted(m for m in messages if m not in cat_m),
        "orphan_messages": sorted(m for m in cat_m if m not in messages),
    }


def extract(lang: str) -> dict[str, dict[str, str]]:
    """Squelette de catalogue : les textes déjà traduits sont gardés, les nouveaux reprennent le français."""
    texts = modifier_texts()
    messages = source_messages(project_roots())
    cat_t = i18n.catalog(lang, "modifiers")
    cat_m = i18n.catalog(lang, "messages")
    return {
        "modifiers": {k: cat_t.get(k, v) for k, v in sorted(texts.items())},
        "messages": {m: cat_m.get(m, m) for m in sorted(messages)},
    }


def write_catalog(lang: str, data: dict[str, dict[str, str]]) -> Path:
    i18n.LOCALES_DIR.mkdir(parents=True, exist_ok=True)
    path = i18n.LOCALES_DIR / f"{lang}.json"
    path.write_bytes((json.dumps(data, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    i18n.reload()
    return path
