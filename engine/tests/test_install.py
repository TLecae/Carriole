import sys

from etl_engine import install


def fake_install(tmp_path, monkeypatch):
    resources = tmp_path / "resources"
    (resources / "runtime" / "python").mkdir(parents=True)
    (resources / "frontend").mkdir()
    (resources / "runtime" / "duckdb_ext").mkdir()
    (resources / "install.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(resources / "runtime" / "python" / "python.exe"))
    return resources


def test_not_an_installation_by_default():
    assert install.install_root() is None and install.apply_env() is None


def test_installation_is_found_and_sets_variables_without_overriding(tmp_path, monkeypatch):
    resources = fake_install(tmp_path, monkeypatch)
    monkeypatch.delenv("ETL_FRONTEND_DIR", raising=False)
    monkeypatch.delenv("ETL_DUCKDB_EXTENSIONS", raising=False)
    monkeypatch.delenv("PROJ_USER_WRITABLE_DIRECTORY", raising=False)
    monkeypatch.setenv("ETL_DUCKDB_EXTENSIONS", "explicite")  # l'utilisateur garde la main
    assert install.apply_env() == resources
    import os

    assert os.environ["ETL_FRONTEND_DIR"] == str(resources / "frontend")
    assert os.environ["ETL_DUCKDB_EXTENSIONS"] == "explicite"
    assert "PROJ_USER_WRITABLE_DIRECTORY" not in os.environ  # dossier absent : variable non posée
