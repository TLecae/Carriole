import pytest
from etl_engine import settings


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ETL_HOME", str(tmp_path))
    return tmp_path


def test_dirs_follow_etl_home(home):
    dirs = settings.app_dirs()
    assert dirs.meta == home / "data" / "meta"
    assert dirs.cache == home / "data" / "cache"
    assert dirs.settings_file == home / "config" / "settings.json"


def test_missing_file_gives_empty_settings(home):
    assert settings.load() == {}
    assert settings.get("language", "x") == "x"


def test_set_get_unset_nested_and_atomic(home):
    settings.set_value("ai.anthropic.model", "m1")
    assert settings.get("ai.anthropic.model") == "m1"
    assert settings.unset("ai.anthropic.model") is True
    assert settings.unset("ai.anthropic.model") is False
    assert not list((home / "config").glob(".settings-*"))  # pas de fichier temporaire oublié


def test_corrupt_file_is_an_error(home):
    (home / "config").mkdir()
    (home / "config" / "settings.json").write_text("{pas du json", encoding="utf-8")
    with pytest.raises(ValueError, match="illisibles"):
        settings.load()


def test_language_priority(home, monkeypatch):
    monkeypatch.delenv("ETL_LANG", raising=False)
    assert settings.language() == "fr"
    settings.set_value("language", "en")
    assert settings.language() == "en"
    monkeypatch.setenv("ETL_LANG", "FR")
    assert settings.language() == "fr"


def test_concurrent_reads_and_writes_never_fail(home):
    """Le moteur relit les réglages à chaque réponse HTTP pendant que l'interface en écrit : aucune erreur d'accès."""
    import threading

    errors: list[BaseException] = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                settings.get("tile_hosts", [])
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for thread in threads:
        thread.start()
    try:
        for i in range(60):
            settings.set_value("tile_hosts", [f"https://h{i}.example"])
    finally:
        stop.set()
        for thread in threads:
            thread.join()
    assert errors == [] and settings.get("tile_hosts") == ["https://h59.example"]


def test_folders_of_the_former_name_are_renamed_once(tmp_path, monkeypatch):
    """Atelier ETL est devenu Carriole : les runs, flux récents et réglages de l'ancien dossier sont conservés."""
    monkeypatch.delenv("ETL_HOME", raising=False)
    monkeypatch.setattr(settings.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    (tmp_path / "local" / "AtelierETL" / "meta").mkdir(parents=True)
    (tmp_path / "local" / "AtelierETL" / "meta" / "etl.db").write_text("runs", encoding="utf-8")
    (tmp_path / "roaming" / "AtelierETL").mkdir(parents=True)
    dirs = settings.app_dirs()
    assert dirs.data == tmp_path / "local" / "Carriole" and dirs.config == tmp_path / "roaming" / "Carriole"
    assert (dirs.meta / "etl.db").read_text(encoding="utf-8") == "runs"
    assert not (tmp_path / "local" / "AtelierETL").exists()
    (tmp_path / "local" / "AtelierETL").mkdir()  # un ancien dossier recréé ensuite ne remplace rien
    assert settings.app_dirs().data == tmp_path / "local" / "Carriole"


def test_a_former_folder_that_cannot_be_renamed_stays_in_use(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.Path, "rename", lambda self, target: (_ for _ in ()).throw(PermissionError()))
    (tmp_path / "AtelierETL").mkdir()
    assert settings._folder(tmp_path, "Carriole") == tmp_path / "AtelierETL"
