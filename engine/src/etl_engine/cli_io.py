"""Sortie de la ligne de commande : messages traduits dans la langue choisie (`--lang`, `ETL_LANG`, réglage)."""

from __future__ import annotations

import typer

from etl_engine import i18n


def say(message: object = "", *, err: bool = False, nl: bool = True) -> None:
    typer.echo(i18n.tr(str(message)), err=err, nl=nl)
