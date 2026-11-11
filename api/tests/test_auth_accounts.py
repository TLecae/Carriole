"""Comptes locaux (hachage, vérification), proxys de confiance, administrateurs, dossiers du mode serveur."""

from __future__ import annotations

import pytest
from etl_api.auth.accounts import AuthError, LocalAccounts, hash_password, verify_password, write_users
from etl_api.auth.config import AuthConfig
from etl_api.auth.service import trusted


def test_local_accounts_hash_and_check_passwords(tmp_path):
    stored = hash_password("correct horse")
    assert stored.startswith("scrypt$") and "correct" not in stored
    assert verify_password("correct horse", stored) and not verify_password("autre", stored)
    path = tmp_path / "users.json"
    write_users(path, {"Alice": {"password": stored, "groups": ["sig"]}})
    identity = LocalAccounts(path).authenticate("alice", "correct horse")  # identifiant sans casse
    assert (identity.name, identity.groups) == ("Alice", ("sig",))
    for name, password in (("alice", "autre"), ("inconnu", "correct horse"), ("alice", "")):
        with pytest.raises(AuthError):
            LocalAccounts(path).authenticate(name, password)


def test_trusted_proxies_accept_addresses_and_networks():
    assert trusted("10.1.2.3", ("10.1.0.0/16",)) and trusted("::1", ("127.0.0.1", "::1"))
    assert not trusted("10.2.0.1", ("10.1.0.0/16",)) and not trusted("testclient", ("10.1.0.0/16",))


def test_admins_default_to_a_neutral_group(monkeypatch):
    monkeypatch.delenv("ETL_ADMINS", raising=False)
    config = AuthConfig.load()
    assert config.is_admin("x", ["carriole-admins"]) and not config.is_admin("x", ["autre"])


def test_server_mode_without_roots_gives_no_file_access(monkeypatch, tmp_path):
    from etl_engine import paths
    from etl_engine.sdk import ConfigError

    monkeypatch.delenv("ETL_READ_ROOTS", raising=False)
    monkeypatch.setenv("ETL_MODE", "server")
    assert paths.browse_roots("read") == []
    with pytest.raises(ConfigError, match="aucun dossier autorisé"):
        paths.check(tmp_path / "a.csv", "read")
    monkeypatch.setenv("ETL_READ_ROOTS", str(tmp_path))
    assert paths.check(tmp_path / "a.csv", "read")
