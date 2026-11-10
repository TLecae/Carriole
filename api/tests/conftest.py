"""Tests de l'API : les scénarios du mode serveur peuvent viser un store PostgreSQL jetable (`ETL_TEST_STORE_URL`,
`postgresql+psycopg://…`) : ses tables sont effacées avant chaque test."""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def server_store(monkeypatch):
    url = os.environ.get("ETL_TEST_STORE_URL", "").strip()
    if not url:
        yield
        return
    from etl_store.models import Base
    from etl_store.store import make_engine

    engine = make_engine(url)
    Base.metadata.drop_all(engine)
    engine.dispose()
    monkeypatch.setenv("ETL_DB_URL", url)
    yield
