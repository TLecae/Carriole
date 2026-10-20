import json

import pytest
from etl_engine import complib
from etl_engine.flow import Flow
from etl_store import Conflict, FileStore, InvalidFlowFile, NotFound
from etl_store.filestore import flow_path, make_id


def content(name="demo", **extra):
    return Flow.model_validate({"name": name, **extra}).model_dump(mode="json", by_alias=True)


@pytest.fixture
def store(tmp_path):
    return FileStore(f"sqlite:///{(tmp_path / 'etl.db').as_posix()}", tmp_path / "recent.json")


def test_id_roundtrip_keeps_unicode_and_spaces(tmp_path):
    path = tmp_path / "flux été 2026.json"
    assert flow_path(make_id(path)) == path
    assert "/" not in make_id(path) and "=" not in make_id(path)  # sûr dans une URL
    with pytest.raises(NotFound):
        flow_path("n'importe quoi")


def test_create_get_update_and_revision_conflict(store, tmp_path):
    target = tmp_path / "equipe" / "demo"  # extension ajoutée, dossier créé
    created = store.create_flow("demo", content(), "alice", path=str(target))
    assert created["path"].endswith("demo.json") and (tmp_path / "equipe" / "demo.json").is_file()
    got = store.get_flow(created["id"])
    assert got["revision"] == created["revision"] and got["flow"]["name"] == "demo"

    changed = {**content(), "description": "v2"}
    ok = store.update_flow(created["id"], "v2", changed, got["revision"])
    saved = json.loads(target.with_suffix(".json").read_text("utf-8"))
    assert ok["revision"] != got["revision"] and saved["description"] == "v2"
    assert saved["name"] == ok["name"] == "demo"  # le nom est celui du fichier, quel que soit le nom envoyé
    with pytest.raises(Conflict):  # quelqu'un d'autre a enregistré entre-temps
        store.update_flow(created["id"], "v3", content("v3"), got["revision"])
    # sans révision : écrasement volontaire
    assert store.update_flow(created["id"], "v3", content("v3"), None)["name"] == "demo"


def test_a_flow_is_named_after_its_file(store, tmp_path):
    path = tmp_path / "rpg_hte-garonne.json"
    path.write_text(json.dumps({**content(), "name": "ancien nom"}), encoding="utf-8")
    opened = store.open_flow_file(str(path))
    assert opened["name"] == opened["flow"]["name"] == "rpg_hte-garonne"
    assert [f["name"] for f in store.list_flows()] == ["rpg_hte-garonne"]


def test_creating_over_an_existing_file_is_refused(store, tmp_path):
    store.create_flow("a", content("a"), "u", path=str(tmp_path / "a.json"))
    with pytest.raises(Conflict, match="existe déjà"):
        store.create_flow("b", content("b"), "u", path=str(tmp_path / "a.json"))
    assert json.loads((tmp_path / "a.json").read_text("utf-8"))["name"] == "a"  # intact
    with pytest.raises(ValueError):
        store.create_flow("c", content("c"), "u")  # chemin obligatoire


def test_file_is_saved_atomically_without_leftovers(store, tmp_path):
    store.create_flow("a", content("a"), "u", path=str(tmp_path / "a.json"))
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".part" or p.name.startswith(".")] == []


def test_missing_and_broken_files(store, tmp_path):
    with pytest.raises(NotFound):
        store.get_flow(make_id(tmp_path / "absent.json"))
    (tmp_path / "casse.json").write_text("{pas du json", encoding="utf-8")
    with pytest.raises(InvalidFlowFile):
        store.open_flow_file(str(tmp_path / "casse.json"))
    (tmp_path / "tableau.json").write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(InvalidFlowFile):
        store.open_flow_file(str(tmp_path / "tableau.json"))


def test_recents_follow_opens_and_saves_without_touching_the_disk(store, tmp_path):
    a = store.create_flow("a", content("a", description="premier"), "u", path=str(tmp_path / "a.json"))
    store.create_flow("b", content("b"), "u", path=str(tmp_path / "b.json"))
    store.open_flow_file(str(tmp_path / "a.json"))  # remonte en tête, sans doublon
    (tmp_path / "b.json").unlink()  # fichier disparu : la liste ne le sait pas (aucun accès disque) mais ne casse pas
    names = [f["name"] for f in store.list_flows()]
    assert names == ["a", "b"]
    assert store.list_flows()[0]["description"] == "premier"
    store.delete_flow(a["id"])  # retire des récents, le fichier reste
    assert [f["name"] for f in store.list_flows()] == ["b"] and (tmp_path / "a.json").is_file()
    with pytest.raises(NotFound):
        store.delete_flow(a["id"])


def test_components_come_from_the_component_folders_and_publish_versions(store, tmp_path, monkeypatch):
    folder = tmp_path / "composants"
    monkeypatch.setenv("ETL_COMPONENTS_DIR", str(folder))
    comp = content("nettoyage", component={"name": "nettoyage", "label": "Nettoyage", "category": "Équipe"})
    (folder).mkdir()
    (folder / "nettoyage.json").write_text(json.dumps(comp), encoding="utf-8")
    (folder / "un_flux_ordinaire.json").write_text(json.dumps(content("ordinaire")), encoding="utf-8")

    assert [c["component"] for c in store.list_components()] == ["nettoyage"]
    assert store.get_component("nettoyage")["flow"]["name"] == "nettoyage"
    assert store.get_component("absent") is None and store.latest_component_versions() == {}

    v1 = store.publish_component("nettoyage", comp, "première", "alice")
    assert v1["version"] == 1 and (folder / "nettoyage.v1.json").is_file()
    with pytest.raises(complib.NothingToPublish):
        store.publish_component("nettoyage", comp, "idem", "alice")
    changed = {**comp, "description": "modifié"}
    assert store.publish_component("nettoyage", changed, "deuxième", "bob")["version"] == 2
    assert [v["version"] for v in store.list_component_versions("nettoyage")] == [2, 1]
    assert store.get_component_version("nettoyage", 1)["flow"]["description"] in ("", None)
    assert store.get_component_version("nettoyage")["version"] == 2
    assert store.latest_component_versions() == {"nettoyage": 2}
    with pytest.raises(NotFound):
        store.publish_component("inconnu", comp, "", "x")
    # les fichiers de versions ne sont pas pris pour des composants
    assert [c["component"] for c in store.list_components()] == ["nettoyage"]


def test_a_hand_written_minimal_file_is_completed_for_the_interface(store, tmp_path):
    (tmp_path / "minimal.json").write_text(json.dumps({"name": "À la main", "nodes": [], "links": []}), "utf-8")
    opened = store.open_flow_file(str(tmp_path / "minimal.json"))
    assert opened["flow"]["bookmarks"] == [] and opened["flow"]["annotations"] == []
    assert opened["flow"]["published_params"] == []
    assert (
        json.loads((tmp_path / "minimal.json").read_text("utf-8")).get("bookmarks") is None
    )  # le fichier n'est pas réécrit
