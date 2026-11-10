"""Base des tests du store : SQLite (fichier temporaire), ou PostgreSQL quand `ETL_TEST_STORE_URL` désigne une base
jetable (`postgresql+psycopg://…`) : ses tables sont effacées avant chaque test."""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def store_url(tmp_path) -> str:
    url = os.environ.get("ETL_TEST_STORE_URL", "").strip()
    if not url:
        return f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    from etl_store.models import Base
    from etl_store.store import make_engine

    engine = make_engine(url)
    Base.metadata.drop_all(engine)
    engine.dispose()
    return url
