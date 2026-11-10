"""Les tests ne touchent jamais aux dossiers réels de l'utilisateur (%LOCALAPPDATA%, %APPDATA%)."""

import pytest


@pytest.fixture(autouse=True, scope="session")
def _isolated_home(tmp_path_factory):
    import os

    previous = os.environ.get("ETL_HOME")
    os.environ["ETL_HOME"] = str(tmp_path_factory.mktemp("etl-home"))
    yield
    if previous is None:
        os.environ.pop("ETL_HOME", None)
    else:
        os.environ["ETL_HOME"] = previous


def _store_test_url() -> str:
    """`ETL_TEST_STORE_URL` : base PostgreSQL jetable (`postgresql+psycopg://…`) où tournent les tests du store et les
    scénarios du mode serveur ; ses tables sont effacées avant chaque test. Vide : SQLite."""
    import os

    url = os.environ.get("ETL_TEST_STORE_URL", "").strip()
    if url:
        from etl_store.models import Base
        from etl_store.store import make_engine

        engine = make_engine(url)
        Base.metadata.drop_all(engine)
        engine.dispose()
    return url


@pytest.fixture
def store_url(tmp_path) -> str:
    """Tests du store : SQLite dans un fichier temporaire, ou PostgreSQL (`ETL_TEST_STORE_URL`)."""
    return _store_test_url() or f"sqlite:///{(tmp_path / 'm.db').as_posix()}"


@pytest.fixture
def server_store(monkeypatch):
    """Scénarios du mode serveur (API) : leur store vise PostgreSQL quand `ETL_TEST_STORE_URL` est défini."""
    url = _store_test_url()
    if url:
        monkeypatch.setenv("ETL_DB_URL", url)
