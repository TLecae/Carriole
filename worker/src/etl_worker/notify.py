"""Notification de fin de run par e-mail (facultative)."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Any

from etl_store import Settings

log = logging.getLogger("etl.worker")

LABELS = {"done": "terminé", "error": "en échec", "cancelled": "annulé", "interrupted": "interrompu"}


def build_message(
    settings: Settings, run: dict[str, Any], status: str, error: str | None, result: dict[str, Any] | None
) -> EmailMessage:
    to = run["options"]["notify_email"]
    label = LABELS.get(status, status)
    msg = EmailMessage()
    msg["Subject"] = f"[Carriole] {run['flow_name']} : run {label}"
    msg["From"] = settings.smtp_from
    msg["To"] = to
    lines = [f"Le run du flux « {run['flow_name']} » est {label}."]
    if result:
        lines.append(f"Durée : {result.get('seconds', 0):.1f} s.")
        failed = [f"{n} ({r['error']})" for n, r in result.get("nodes", {}).items() if r.get("status") == "error"]
        if failed:
            lines.append("Nœuds en erreur : " + ", ".join(failed))
        rejected = sum(r.get("rows", {}).get("Rejected", 0) for r in result.get("nodes", {}).values())
        if rejected:
            lines.append(f"{rejected} entité(s) rejetée(s) (port Rejected).")
    if error:
        lines.append(f"Erreur : {error}")
    if settings.public_url and run.get("flow_id"):
        lines.append(f"Ouvrir le flux : {settings.public_url}/#flow={run['flow_id']}")
    msg.set_content("\n".join(lines))
    return msg


def notify_run_finished(
    settings: Settings, run: dict[str, Any], status: str, error: str | None, result: dict[str, Any] | None
) -> bool:
    """Envoie l'e-mail demandé au lancement. Renvoie vrai s'il est parti ; un échec est journalisé, jamais propagé."""
    if not run["options"].get("notify_email"):
        return False
    if not settings.smtp_host:
        log.warning("run %s : e-mail demandé mais ETL_SMTP_HOST n'est pas configuré", run["id"])
        return False
    try:
        msg = build_message(settings, run, status, error, result)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException) as exc:
        log.warning("run %s : envoi de l'e-mail impossible : %s", run["id"], exc)
        return False
