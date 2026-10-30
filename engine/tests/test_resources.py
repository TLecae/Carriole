"""Manque de ressources de la machine (mémoire, disque) : une erreur claire du nœud, ni rejet d'entités ni repli."""

import errno

import duckdb
import pytest
from etl_engine import duck
from etl_engine.sdk import ConfigError
from etl_engine.sqlroute import _duckdb_errors


def test_a_full_disk_says_where_and_how_much_is_free(tmp_path):
    spilled = f"{tmp_path.as_posix()}/.spill/d207/duckdb_temp_storage-3.tmp"
    exc = duckdb.IOException(
        f'IO Error: Could not write file "{spilled}" (error in WriteFile): Espace insuffisant sur le disque.'
    )
    found = duck.resource_error(exc)
    assert isinstance(found, duck.DiskFull)
    assert str(found).startswith(f"espace disque insuffisant sur {tmp_path.anchor}")
    assert "Go libres" in str(found) and "Paramètres" in str(found)


@pytest.mark.parametrize(
    "exc",
    [OSError(errno.ENOSPC, "No space left on device"), OSError("[WinError 112] There is not enough space on the disk")],
)
def test_a_full_disk_seen_by_python_or_arrow(exc):
    assert isinstance(duck.resource_error(exc), duck.DiskFull)


def test_other_errors_are_not_resources():
    assert isinstance(duck.resource_error(duckdb.OutOfMemoryException("Out of Memory Error: x")), MemoryError)
    assert duck.resource_error(duckdb.ConversionException("Could not convert string 'a' to INT32")) is None
    assert duck.resource_error(ValueError("x")) is None


def test_a_full_disk_stops_the_sql_route_without_falling_back():
    with pytest.raises(duck.DiskFull), _duckdb_errors(fallback=True, name="X"):
        raise duckdb.IOException('IO Error: Could not write file "/tmp/x.tmp": No space left on device')
    with _duckdb_errors(fallback=True, name="X") as failed:
        raise duckdb.ConversionException("Could not convert")
    assert failed[0]  # une erreur de donnée : la voie par lots reprend le nœud
    with pytest.raises(ConfigError), _duckdb_errors(fallback=False, name="X"):
        raise duckdb.BinderException("Referenced column not found")
