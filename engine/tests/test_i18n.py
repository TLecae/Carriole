import json
import re

import pytest
from etl_engine import i18n, i18n_tools
from etl_engine.analysis import analyze
from etl_engine.cli import app
from etl_engine.executor import run_flow
from etl_engine.registry import Registry
from typer.testing import CliRunner

from conftest import make_flow


def test_every_text_of_the_code_has_an_english_translation():
    report = i18n_tools.check("en")
    assert report["missing_modifiers"] == []
    assert report["missing_messages"] == []
    assert report["orphan_modifiers"] == []  # une clé inutile signale un texte qui a changé sans son catalogue
    assert report["orphan_messages"] == []


def test_french_is_the_source_and_english_overlays_it(registry):
    cls = registry.get("ExtractByExpression")
    fr, en = cls.describe("fr"), cls.describe("en")
    assert fr["label"] == "Filtrer par expression" and en["label"] == "Extract by expression"
    assert fr["category"] == "Filtres" and en["category"] == "Filters"
    assert en["params_schema"]["properties"]["combine"]["title"] == "Combine with"
    assert fr["params_schema"]["properties"]["combine"]["title"] == "Combiner par"
    assert en["params_schema"]["properties"]["combine"]["default"] == "AND"  # seuls les textes changent
    assert cls.describe("de") == fr  # langue sans catalogue : le français du code


def test_describe_does_not_leak_the_translation_into_the_source(registry):
    cls = registry.get("ExtractByExpression")
    cls.describe("en")
    assert cls.describe("fr")["label"] == "Filtrer par expression"


def test_messages_are_translated_by_template_and_composed_messages_piece_by_piece():
    assert i18n.tr("attribut absent en amont : nom", "en") == "attribute missing upstream: nom"
    assert i18n.tr("n1 : configuration : au moins une condition est nécessaire", "en") == (
        "n1: configuration: at least one condition is required"
    )
    assert i18n.tr("texte inconnu du catalogue", "en") == "texte inconnu du catalogue"
    assert i18n.tr("attribut absent en amont : nom", "fr") == "attribut absent en amont : nom"
    assert i18n.tr("", "en") == ""


def test_language_codes_are_normalized():
    assert i18n.normalize("en-US,en;q=0.9") == "en"
    assert i18n.normalize("FR_fr") == "fr"
    assert i18n.available_languages()[:2] == ["fr", "en"]


def test_new_message_helper_uses_the_catalog():
    assert i18n.t("fichier introuvable : {path}", "en", path="a.csv") == "file not found: a.csv"
    assert i18n.t("message jamais traduit : {x}", "en", x=1) == "message jamais traduit : 1"  # repli : le français
    assert i18n.t("fichier introuvable : {path}", "fr", path="a.csv") == "fichier introuvable : a.csv"
    assert i18n.t("run introuvable", "en") == "run not found"


def test_run_errors_and_logs_follow_the_requested_language(registry, tmp_path):
    flow = make_flow([{"id": "n1", "type": "ExtractByExpression", "params": {"conditions": []}}], [])
    events: list[dict] = []
    result = run_flow(flow, registry, tmp_path / "en", on_event=events.append, lang="en")
    assert result.status == "error"
    assert "at least one condition is required" in result.error
    assert result.nodes["n1"].error and "at least one condition" in result.nodes["n1"].error
    assert "at least one condition" in [e for e in events if e["type"] == "run_finished"][0]["error"]
    result_fr = run_flow(flow, registry, tmp_path / "fr", lang="fr")
    assert "au moins une condition est nécessaire" in result_fr.error


def test_analysis_issues_follow_the_requested_language(registry):
    flow = make_flow([{"id": "n1", "type": "ExtractByExpression", "params": {"conditions": []}}], [])
    en = [i["message"] for i in analyze(flow, registry, None, "en")["issues"]]
    fr = [i["message"] for i in analyze(flow, registry, None, "fr")["issues"]]
    assert "at least one condition is required" in en and 'input "input" not connected' in en
    assert "au moins une condition est nécessaire" in fr and "entrée « input » non connectée" in fr


def test_plugin_catalog_translates_a_plugin(tmp_path):
    plugin = tmp_path / "mon_plugin.py"
    plugin.write_text(
        "from etl_engine.sdk import Modifier\n"
        "class MonPlugin(Modifier):\n"
        "    name = 'MonPlugin'\n"
        "    label = 'Mon plugin'\n"
        "    description = 'Ne fait rien.'\n",
        encoding="utf-8",
    )
    (tmp_path / "mon_plugin.locales.json").write_text(
        json.dumps({"en": {"modifiers": {"MonPlugin.label": "My plugin"}}}), encoding="utf-8"
    )
    try:
        registry = Registry.discover(tmp_path)
        cls = registry.get("MonPlugin")
        assert cls.describe("en")["label"] == "My plugin"
        assert cls.describe("en")["description"] == "Ne fait rien."  # clé absente : texte du code
        assert cls.describe("fr")["label"] == "Mon plugin"
    finally:
        i18n._extra_files.clear()
        i18n.reload()


def test_language_follows_the_setting_and_the_cli_option(monkeypatch, tmp_path):
    flow = tmp_path / "f.json"
    flow.write_text(
        json.dumps(
            {
                "name": "t",
                "nodes": [{"id": "n1", "type": "ExtractByExpression", "params": {"conditions": []}}],
                "links": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ETL_LANG", "fr")  # restauré à la fin : l option --lang écrit dans l environnement
    fr = CliRunner().invoke(app, ["validate", str(flow)])
    assert "au moins une condition est nécessaire" in fr.stdout
    en = CliRunner().invoke(app, ["--lang", "en", "validate", str(flow)])
    assert "at least one condition is required" in en.stdout
    assert "2 item(s)" in en.stdout


def test_i18n_check_command_reports_a_missing_language():
    result = CliRunner().invoke(app, ["i18n", "check", "--lang", "zz"])
    assert result.exit_code == 1
    assert "sans traduction" in result.stdout


def test_extract_creates_a_skeleton_for_a_new_language(tmp_path):
    out = tmp_path / "de.json"
    result = CliRunner().invoke(app, ["i18n", "extract", "de", "--out", str(out)])
    assert result.exit_code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["modifiers"]["ExtractByExpression.label"] == "Filtrer par expression"  # repris du français, à traduire
    assert "run introuvable" in data["messages"]


@pytest.mark.parametrize("lang", ["en"])
def test_translations_keep_their_placeholders(lang):
    """Une traduction qui perd ou invente un `{nom}` casserait le message."""
    import re

    pattern = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
    for fr, target in i18n.catalog(lang, "messages").items():
        assert set(pattern.findall(fr)) == set(pattern.findall(target)), (fr, target)


def test_messages_that_contain_braces_keep_them():
    assert i18n.tr("paramètre publié inconnu : ${FOO}", "en") == "unknown published parameter: ${FOO}"
    assert i18n.tr("variable ${var:DEP} : aucun nœud ne la pose en amont", "en") == (
        "variable ${var:DEP}: no node sets it upstream"
    )
    assert i18n.tr("variable globale inconnue : ${global:X} (réglage « variables »)", "en") == (
        'unknown global variable: ${global:X} ("variables" setting)'
    )


def test_no_english_entry_is_a_copy_of_the_french():
    """Un squelette (`etl i18n extract`) recopie le français : il ne doit pas rester tel quel dans le catalogue."""
    accents = re.compile("[àâçéèêëîïôûùüœÀÉÈÊ]")
    copies = [k for k, v in i18n.catalog("en", "messages").items() if k == v and accents.search(k)]
    source = i18n_tools.modifier_texts()
    copies += [k for k, v in i18n.catalog("en", "modifiers").items() if v == source.get(k) and accents.search(v)]
    assert copies == []
