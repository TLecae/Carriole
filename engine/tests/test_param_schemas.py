"""Les paramètres sont rendus en formulaire : libellés français et widgets métier survivent dans le JSON Schema."""

import pytest


def props(registry, name):
    return registry.get(name).describe()["params_schema"]


def test_widgets_survive_french_titles(registry):
    keeper = props(registry, "RetainAttributes")["properties"]["attributes"]
    assert keeper["x-etl-widget"] == "attributes" and keeper["title"] == "Attributs à garder"
    reader = props(registry, "Reader")["properties"]
    assert reader["path"]["x-etl-widget"] == "path" and reader["crs"]["x-etl-widget"] == "crs"
    defs = props(registry, "AddAttributes")["$defs"]["AttributeSpec"]["properties"]
    assert defs["expression"]["x-etl-widget"] == "expression" and defs["expression"]["title"] == "Expression"
    rule = props(registry, "RefactorAttributes")["$defs"]["Rule"]["properties"]
    assert rule["attribute"]["x-etl-widget"] == "attribute"
    renaming = props(registry, "RenameAttributes")["$defs"]["Renaming"]["properties"]
    assert renaming["source"]["x-etl-widget"] == "attribute" and renaming["target"]["title"] == "Nouveau nom"


def all_names() -> list[str]:
    from etl_engine.registry import Registry

    return sorted(c.name for c in Registry.discover().all())


@pytest.mark.parametrize("name", all_names())
def test_multiword_parameters_have_a_french_title(registry, name):
    """Un nom composé (new_name, keep_geometry…) sans titre explicite s'afficherait en anglais."""
    schema = props(registry, name)
    fields = dict(schema.get("properties", {}))
    for d in schema.get("$defs", {}).values():
        fields.update(d.get("properties", {}))
    english = [k for k, v in fields.items() if "_" in k and v.get("title") == k.replace("_", " ").title()]
    assert not english, f"{name} : paramètres sans libellé français : {english}"


@pytest.mark.parametrize("name", all_names())
def test_every_modifier_is_documented_for_the_palette(registry, name):
    info = registry.get(name).describe()
    assert info["label"] and info["category"] and info["description"], (
        f"{name} : libellé, catégorie ou description vide"
    )
    assert info["help"], f"{name} : aide vide"
    assert info["label"] != name or " " not in info["label"]
