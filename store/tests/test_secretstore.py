import sys

import pytest
from etl_store import Settings, secretstore


def test_plain_key_is_returned_stripped():
    assert secretstore.unprotect(b"abc\n") == b"abc"


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI : Windows seulement")
def test_dpapi_roundtrip_and_binary_blob_is_not_stripped():
    blob = secretstore.protect(b"cle-secrete")
    assert blob.startswith(secretstore.PREFIX)
    assert b"cle-secrete" not in blob
    assert secretstore.unprotect(blob) == b"cle-secrete"


def test_key_file_is_created_once_and_stable(tmp_path, monkeypatch):
    for name in ("ETL_SECRET_KEY", "ETL_SECRET_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(meta_dir=tmp_path)
    first = settings.secret_key
    assert settings.secret_key == first
    assert len(first) == 44  # clé Fernet encodée en base64
    if secretstore.available():
        assert (tmp_path / "secret.key").read_bytes().startswith(secretstore.PREFIX)


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI : Windows seulement")
def test_legacy_plain_key_is_protected_in_place(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet

    for name in ("ETL_SECRET_KEY", "ETL_SECRET_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)
    legacy = Fernet.generate_key()
    (tmp_path / "secret.key").write_bytes(legacy + b"\n")
    assert Settings(meta_dir=tmp_path).secret_key == legacy
    assert (tmp_path / "secret.key").read_bytes().startswith(secretstore.PREFIX)
    assert Settings(meta_dir=tmp_path).secret_key == legacy
