"""Configuration : variables `ETL_*`, sinon dossiers de l'application (`etl_engine.settings`, docs/installation.md)."""

from __future__ import annotations

import contextlib
import getpass
import hashlib
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from etl_engine import settings as user_settings

from etl_store import secretstore

log = logging.getLogger("etl.store")


def _path(name: str, default: Path) -> Path:
    return Path(os.environ.get(name) or default)


def _key_file(path: Path, generate: Callable[[], bytes]) -> bytes:
    """Contenu du fichier de clé, créé (protégé) s'il manque."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "xb") as handle:  # « x » : deux processus ne se marchent pas dessus
                handle.write(secretstore.protect(generate()))
            path.chmod(0o600)
        except FileExistsError:
            pass
    return path.read_bytes()


def _os_user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - aucun compte identifiable
        return "anonyme"


@dataclass(frozen=True)
class Settings:
    meta_dir: Path = field(default_factory=lambda: _path("ETL_META_DIR", user_settings.app_dirs().meta))
    cache_dir: Path = field(default_factory=lambda: _path("ETL_CACHE_DIR", user_settings.app_dirs().cache))
    plugins_dir: Path = field(default_factory=lambda: _path("ETL_PLUGINS_DIR", user_settings.app_dirs().plugins))
    db_url: str = ""  # vide : SQLite dans meta_dir
    # Stockage des flux : « files » (poste de bureau, fichiers JSON) ou « db » (flux dans la base, mode serveur).
    flow_storage: str = field(default_factory=lambda: os.environ.get("ETL_FLOW_STORAGE", "db"))
    # Poste de bureau : l'utilisateur est le compte Windows ; serveur : l'en-tête X-ETL-User du proxy prime.
    default_user: str = field(default_factory=lambda: os.environ.get("ETL_DEFAULT_USER") or _os_user())
    max_running: int = field(default_factory=lambda: int(os.environ.get("ETL_MAX_RUNNING", "4")))
    max_running_per_user: int = field(default_factory=lambda: int(os.environ.get("ETL_MAX_RUNNING_PER_USER", "2")))
    stale_seconds: float = field(default_factory=lambda: float(os.environ.get("ETL_STALE_SECONDS", "30")))
    cache_retention_days: float = field(default_factory=lambda: float(os.environ.get("ETL_CACHE_RETENTION_DAYS", "14")))
    cache_max_bytes: int = field(default_factory=lambda: int(float(os.environ.get("ETL_CACHE_MAX_GB", "50")) * 2**30))
    purge_interval: float = field(default_factory=lambda: float(os.environ.get("ETL_PURGE_INTERVAL", "3600")))
    # « session » (poste de bureau, posé par `etl serve`) : cache gardé seulement tant que son flux est ouvert dans une
    # page ; « retention » (serveur) : gardé `cache_retention_days` jours. Voir etl_store.purge.release_cache.
    cache_policy: str = field(default_factory=lambda: os.environ.get("ETL_CACHE_POLICY", "retention"))
    cache_hold_ttl: float = field(default_factory=lambda: float(os.environ.get("ETL_CACHE_HOLD_TTL", "90")))
    # Notification de fin de run par e-mail (facultative : sans ETL_SMTP_HOST, seule l'interface notifie)
    smtp_host: str = field(default_factory=lambda: os.environ.get("ETL_SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: int(os.environ.get("ETL_SMTP_PORT", "587")))
    smtp_user: str = field(default_factory=lambda: os.environ.get("ETL_SMTP_USER", ""))
    smtp_from: str = field(default_factory=lambda: os.environ.get("ETL_SMTP_FROM", "carriole@localhost"))
    smtp_starttls: bool = field(default_factory=lambda: os.environ.get("ETL_SMTP_STARTTLS", "1") not in ("0", "false"))
    public_url: str = field(default_factory=lambda: os.environ.get("ETL_PUBLIC_URL", "").rstrip("/"))
    # API locale (poste de bureau) : jeton exigé et hôtes acceptés (anti « DNS rebinding »). Vide : pas de contrôle.
    api_token: str = field(default_factory=lambda: os.environ.get("ETL_API_TOKEN", ""))
    allowed_hosts: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            h.strip().lower() for h in os.environ.get("ETL_ALLOWED_HOSTS", "").split(",") if h.strip()
        )
    )
    frontend_dir: Path | None = field(
        default_factory=lambda: Path(os.environ["ETL_FRONTEND_DIR"]) if os.environ.get("ETL_FRONTEND_DIR") else None
    )
    run_timeout: float | None = field(
        default_factory=lambda: float(os.environ["ETL_RUN_TIMEOUT"]) if os.environ.get("ETL_RUN_TIMEOUT") else None
    )

    @property
    def smtp_password(self) -> str:
        """Mot de passe SMTP : fichier de secret (`ETL_SMTP_PASSWORD_FILE`) de préférence, sinon variable."""
        path = os.environ.get("ETL_SMTP_PASSWORD_FILE")
        return Path(path).read_text(encoding="utf-8").strip() if path else os.environ.get("ETL_SMTP_PASSWORD", "")

    @property
    def secret_key(self) -> bytes:
        """Clé Fernet des secrets (mots de passe de connexions) : `ETL_SECRET_KEY`, sinon `ETL_SECRET_KEY_FILE`,
        sinon un fichier `secret.key` créé dans le dossier des métadonnées au premier besoin (sous Windows, protégé
        par DPAPI : lisible par le seul compte qui l'a créée ; une ancienne clé en clair est protégée au passage)."""
        from cryptography.fernet import Fernet

        if os.environ.get("ETL_SECRET_KEY"):
            return os.environ["ETL_SECRET_KEY"].strip().encode()
        explicit = os.environ.get("ETL_SECRET_KEY_FILE")
        path = Path(explicit) if explicit else self.meta_dir / "secret.key"
        raw = _key_file(path, Fernet.generate_key)
        try:
            key = secretstore.unprotect(raw)
        except OSError:
            if explicit:
                raise
            # protégée par un autre compte Windows ou sur un autre poste (profil copié, mot de passe réinitialisé par un
            # administrateur) : mise de côté et remplacée, plutôt qu'un moteur qui ne démarre plus. Les mots de passe
            # enregistrés avec l'ancienne sont à ressaisir (ConnectionStore, ChatStore le disent).
            aside = path.with_name(f"{path.name}.illisible-{hashlib.sha256(raw).hexdigest()[:12]}")
            with contextlib.suppress(FileNotFoundError, FileExistsError):
                if path.read_bytes() == raw:  # pas la nouvelle clé d'un autre processus
                    os.rename(path, aside)
            log.warning("clé de chiffrement illisible pour ce compte : mise de côté (%s), nouvelle clé créée", aside)
            raw = _key_file(path, Fernet.generate_key)
            key = secretstore.unprotect(raw)
        if secretstore.available() and not explicit and not raw.startswith(secretstore.PREFIX):
            path.write_bytes(secretstore.protect(key))  # ancienne clé en clair : on la protège
        return key

    @property
    def url(self) -> str:
        explicit = self.db_url or os.environ.get("ETL_DB_URL", "")
        if explicit:
            return explicit
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(self.meta_dir / 'etl.db').as_posix()}"
