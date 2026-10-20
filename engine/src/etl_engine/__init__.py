"""Moteur ETL : exécution de flux, SDK des modificateurs, CLI."""

from etl_engine import install

__version__ = "0.1.0"

install.apply_env()  # ressources de l'installation de bureau (aucun effet hors installation)
