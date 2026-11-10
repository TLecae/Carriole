"""Authentification du mode serveur (ADR 0052) : identité, fournisseurs (LDAP, proxy, fichier), sessions."""

from etl_api.auth.config import AuthConfig, server_mode
from etl_api.auth.identity import Identity
from etl_api.auth.providers import AuthError, AuthUnavailable
from etl_api.auth.service import AuthService, ServerGuard

__all__ = ["AuthConfig", "AuthError", "AuthService", "AuthUnavailable", "Identity", "ServerGuard", "server_mode"]
