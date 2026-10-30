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


def test_key_protected_by_another_account_is_set_aside_and_replaced(tmp_path, monkeypatch):
    # clé DPAPI d'un autre compte ou d'un autre poste : le moteur ne démarrait plus (« DPAPI : échec de la lecture »)
    for name in ("ETL_SECRET_KEY", "ETL_SECRET_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)
    foreign = secretstore.PREFIX + b"\x01\x00\x00\x00 blob d'un autre compte"
    (tmp_path / "secret.key").write_bytes(foreign)
    key = Settings(meta_dir=tmp_path).secret_key
    assert len(key) == 44
    assert Settings(meta_dir=tmp_path).secret_key == key
    aside = list(tmp_path.glob("secret.key.illisible-*"))
    assert len(aside) == 1 and aside[0].read_bytes() == foreign


def test_unreadable_explicit_key_file_is_an_error(tmp_path, monkeypatch):
    monkeypatch.delenv("ETL_SECRET_KEY", raising=False)
    (tmp_path / "cle").write_bytes(secretstore.PREFIX + b"\x01\x00\x00\x00 blob d'un autre compte")
    monkeypatch.setenv("ETL_SECRET_KEY_FILE", str(tmp_path / "cle"))
    with pytest.raises(OSError):
        _ = Settings(meta_dir=tmp_path).secret_key
    assert (tmp_path / "cle").exists()


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
