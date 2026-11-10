"""Fournisseurs d'identité : LDAP (connexion simulée de ldap3), fichier de comptes, dossiers du mode serveur."""

from __future__ import annotations

import pytest
from etl_api.auth.config import AuthConfig, LdapConfig
from etl_api.auth.providers import (
    AuthError,
    FileProvider,
    LdapProvider,
    hash_password,
    trusted,
    verify_password,
    write_users,
)
from ldap3 import MOCK_SYNC, Connection, Server

BASE = "dc=exemple,dc=org"
PEOPLE = f"ou=People,{BASE}"
GROUPS = f"ou=Groupes,{BASE}"
SERVICE = f"cn=service,{BASE}"


@pytest.fixture
def directory():
    server = Server("annuaire-simule")
    seed = Connection(server, user=SERVICE, password="secret-service", client_strategy=MOCK_SYNC)
    add = seed.strategy.add_entry
    add(SERVICE, {"userPassword": "secret-service", "objectClass": "person", "cn": "service"})
    add(
        f"uid=alice,{PEOPLE}",
        {"userPassword": "pa", "uid": "alice", "givenName": "Alice", "sn": "Martin", "objectClass": "inetOrgPerson"},
    )
    add(f"uid=bruno,{PEOPLE}", {"userPassword": "pb", "uid": "bruno", "cn": "Bruno", "objectClass": "inetOrgPerson"})
    add(f"cn=equipe,{GROUPS}", {"cn": "equipe", "member": [f"uid=alice,{PEOPLE}"], "objectClass": "groupOfNames"})
    add(f"cn=sig,{GROUPS}", {"cn": "sig", "uniqueMember": [f"uid=alice,{PEOPLE}"], "objectClass": "groupOfUniqueNames"})
    return server


def provider(server, **overrides) -> LdapProvider:
    values = {
        "url": "ldap://annuaire.exemple.org",
        "starttls": False,
        "ca_file": "",
        "bind_dn": SERVICE,
        "bind_password": "secret-service",
        "user_base": PEOPLE,
        "user_filter": "(uid={username})",
        "username_attribute": "uid",
        "display_attributes": ("givenName", "sn"),
        "group_base": GROUPS,
        "group_filter": "(|(member={dn})(uniqueMember={dn})(memberUid={username}))",
        "group_name_attribute": "cn",
        "required_group": "",
        "timeout": 5.0,
    }
    values.update(overrides)

    def connect(user, password):
        return Connection(server, user=user, password=password, client_strategy=MOCK_SYNC)

    return LdapProvider(LdapConfig(**values), connect=connect)


def test_ldap_login_reads_name_display_and_groups(directory):
    identity = provider(directory).authenticate("alice", "pa")
    assert (identity.name, identity.display, identity.groups) == ("alice", "Alice Martin", ("equipe", "sig"))


@pytest.mark.parametrize(
    ("username", "password"),
    [("alice", "mauvais"), ("alice", ""), ("inconnu", "pa"), ("*", "pa"), ("alice)(uid=*", "pa")],
)
def test_ldap_refuses_wrong_or_empty_passwords_and_filter_injection(directory, username, password):
    with pytest.raises(AuthError, match="incorrect"):
        provider(directory).authenticate(username, password)


def test_ldap_required_group_by_name_or_by_dn(directory):
    assert provider(directory, required_group="equipe").authenticate("alice", "pa").name == "alice"
    by_dn = provider(directory, required_group=f"cn=equipe,{GROUPS}")
    assert by_dn.authenticate("alice", "pa").name == "alice"
    with pytest.raises(AuthError, match="groupe requis"):
        by_dn.authenticate("bruno", "pb")
    with pytest.raises(AuthError, match="groupe requis"):
        provider(directory, required_group="equipe").authenticate("bruno", "pb")


def test_ldap_without_group_base_still_checks_a_required_group_dn(directory):
    checked = provider(directory, group_base="", required_group=f"cn=equipe,{GROUPS}")
    identity = checked.authenticate("alice", "pa")
    assert identity.groups == ()
    with pytest.raises(AuthError, match="groupe requis"):
        checked.authenticate("bruno", "pb")


def test_ldap_display_falls_back_to_the_name(directory):
    assert provider(directory, display_attributes=("displayName",)).authenticate("bruno", "pb").display == "bruno"


def test_ldap_settings_problems_name_the_variable():
    config = LdapConfig.load()
    assert any("ETL_LDAP_URL" in p for p in config.problems())


def test_file_provider_hashes_and_checks_passwords(tmp_path):
    stored = hash_password("correct horse")
    assert stored.startswith("scrypt$") and "correct" not in stored
    assert verify_password("correct horse", stored) and not verify_password("autre", stored)
    path = tmp_path / "users.json"
    write_users(path, {"Alice": {"password": stored, "groups": ["sig"]}})
    identity = FileProvider(path).authenticate("alice", "correct horse")  # identifiant sans casse
    assert (identity.name, identity.groups) == ("Alice", ("sig",))
    for name, password in (("alice", "autre"), ("inconnu", "correct horse"), ("alice", "")):
        with pytest.raises(AuthError):
            FileProvider(path).authenticate(name, password)


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
