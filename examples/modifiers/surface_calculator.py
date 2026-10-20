"""Exemple documenté de modificateur : calcul de surface.

Copier ce fichier dans le dossier de plugins (`ETL_PLUGINS_DIR`, par défaut `plugins/`) :
le modificateur apparaît dans la palette de l'éditeur au prochain démarrage, sans rien reconstruire.

Un modificateur = une classe dans un fichier. Le moteur lui donne des **lots** d'entités (tables Arrow,
géométrie en WKB dans la colonne `geometry`) ; il rend un lot par port de sortie.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pyarrow as pa
import shapely
from etl_engine.data import GEOM, geometries, get_crs
from etl_engine.sdk import REJECTED, ConfigError, Modifier, Params, reject


class SurfaceCalculator(Modifier):
    # --- Déclaration : ce que l'éditeur affiche ---------------------------------------
    name = "SurfaceCalculator"  # identifiant stable, utilisé dans les flux enregistrés
    label = "Calculer la surface"  # libellé de la palette
    category = "Géométrie"  # dossier de la palette
    description = "Ajoute la surface de chaque polygone dans un attribut."
    help = (
        "La surface est calculée dans l'unité du système de coordonnées (m² en Lambert-93). "
        "Les entités sans polygone (point, ligne, géométrie vide) partent sur « Rejected »."
    )
    # Ports : une entrée « input », une sortie « output ». Le port « Rejected » est ajouté automatiquement.
    inputs = ("input",)
    outputs = ("output",)
    # Mode « streaming » (défaut) : chaque lot est traité indépendamment, le moteur peut les paralléliser.
    # Un modificateur qui a besoin de toutes les données (tri, agrégation) surcharge `run` à la place.

    # --- Paramètres : l'éditeur en fait un formulaire ---------------------------------
    class Params(Params):
        attribute: str = "surface"  # nom de l'attribut créé
        unit: Literal["m2", "ha"] = "m2"  # liste de choix dans le formulaire
        decimals: int = 2

    # --- Schéma de sortie, sans exécuter : permet de proposer l'attribut en aval ------
    def output_schema(self, inputs: dict[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        schema = inputs.get("input")
        if schema is None:
            return {"output": None}
        if GEOM not in schema.names:
            raise ConfigError("le modificateur a besoin d'une géométrie en entrée")
        if self.params.attribute in schema.names:
            return {"output": schema}
        return {"output": schema.append(pa.field(self.params.attribute, pa.float64()))}

    # --- Traitement d'un lot ------------------------------------------------------------
    def process(self, batch: pa.Table) -> dict[str, pa.Table]:
        geoms = geometries(batch)
        is_polygon = np.isin(shapely.get_type_id(geoms), (3, 6))  # Polygon, MultiPolygon
        area = np.round(shapely.area(geoms), self.params.decimals)
        if self.params.unit == "ha":
            area = np.round(area / 10_000, self.params.decimals)
        values = pa.array(np.where(is_polygon, area, np.nan), mask=~is_polygon)

        ok = batch.filter(pa.array(is_polygon))
        if self.params.attribute in ok.column_names:
            ok = ok.drop_columns([self.params.attribute])
        ok = ok.append_column(self.params.attribute, values.filter(pa.array(is_polygon)))
        out = {
            "output": ok.replace_schema_metadata(
                {b"etl.crs": (get_crs(batch) or "").encode()} if get_crs(batch) else None
            )
        }

        if not is_polygon.all():  # entités en erreur : on les renvoie avec le motif, le run continue
            out[REJECTED] = reject(batch.filter(pa.array(~is_polygon)), "pas de polygone")
        return out
