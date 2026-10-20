"""Explorateur de fichiers : seulement les dossiers autorisés, jamais au-delà."""

import os

import pytest
from etl_api.main import create_app
from etl_store import Settings
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    sources, out, other = tmp_path / "sources", tmp_path / "out", tmp_path / "ailleurs"
    for d in (sources / "2025", out, other):
        d.mkdir(parents=True)
    (sources / "communes.gpkg").write_bytes(b"x" * 10)
    (sources / "notes.txt").write_text("hello", encoding="utf-8")
    (sources / ".cache").mkdir()
    (sources / "2025" / "ventes.csv").write_text("a;b\n", encoding="utf-8")
    (other / "secret.csv").write_text("a\n", encoding="utf-8")
    monkeypatch.setenv("ETL_READ_ROOTS", os.pathsep.join([str(sources), str(out)]))
    monkeypatch.setenv("ETL_WRITE_ROOTS", str(out))
    monkeypatch.setenv("ETL_ROOT_LABELS", f"{sources}=Sources (T:)")
    settings = Settings(meta_dir=tmp_path / "meta", cache_dir=tmp_path / "cache", plugins_dir=tmp_path / "plugins")
    with TestClient(create_app(settings)) as c:
        c.dirs = (sources, out, other)
        yield c


def test_without_path_lists_the_allowed_roots_with_their_label(client):
    sources, out, _ = client.dirs
    body = client.get("/api/files").json()
    assert [(r["path"], r["label"]) for r in body["roots"]] == [
        (str(sources.resolve()), "Sources (T:)"),
        (str(out.resolve()), str(out.resolve())),
    ]
    assert body["entries"] == [] and body["parent"] is None


def test_lists_folders_first_and_hides_dotfiles(client):
    sources, _, _ = client.dirs
    body = client.get("/api/files", params={"path": str(sources)}).json()
    assert [(e["name"], e["kind"]) for e in body["entries"]] == [
        ("2025", "dir"),
        ("communes.gpkg", "file"),
        ("notes.txt", "file"),
    ]
    assert body["entries"][1]["size"] == 10
    assert body["parent"] is None  # à la racine d'un dossier autorisé : l'explorateur remonte à la liste des racines


def test_subfolder_has_a_parent_and_extensions_filter(client):
    sources, _, _ = client.dirs
    sub = client.get("/api/files", params={"path": str(sources / "2025")}).json()
    assert sub["parent"] == str(sources.resolve()) and [e["name"] for e in sub["entries"]] == ["ventes.csv"]
    gpkg = client.get("/api/files", params={"path": str(sources), "extensions": "gpkg,.shp"}).json()
    assert [e["name"] for e in gpkg["entries"]] == ["2025", "communes.gpkg"]  # les dossiers restent navigables


def test_recognized_formats_only(client):
    sources, _, _ = client.dirs
    (sources / "photo.jpg").write_bytes(b"x")
    body = client.get("/api/files", params={"path": str(sources), "recognized": "true"}).json()
    assert [e["name"] for e in body["entries"]] == ["2025", "communes.gpkg", "notes.txt"]  # .txt se lit comme un CSV


def test_refuses_outside_the_roots_and_dot_dot(client):
    sources, _, other = client.dirs
    assert client.get("/api/files", params={"path": str(other)}).status_code == 403
    assert client.get("/api/files", params={"path": str(sources / ".." / "ailleurs")}).status_code == 403
    assert (
        client.get("/api/files", params={"path": str(sources / "communes.gpkg")}).status_code == 404
    )  # pas un dossier


def test_write_mode_only_offers_write_roots(client):
    sources, out, _ = client.dirs
    roots = client.get("/api/files", params={"mode": "write"}).json()["roots"]
    assert [r["path"] for r in roots] == [str(out.resolve())]
    assert client.get("/api/files", params={"path": str(sources), "mode": "write"}).status_code == 403


def test_symlink_leaving_the_roots_is_not_listed(client):
    sources, _, other = client.dirs
    try:
        (sources / "lien.csv").symlink_to(other / "secret.csv")
    except (OSError, NotImplementedError):
        pytest.skip("liens symboliques non disponibles sur ce poste")
    names = [e["name"] for e in client.get("/api/files", params={"path": str(sources)}).json()["entries"]]
    assert "lien.csv" not in names
