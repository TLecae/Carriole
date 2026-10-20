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
