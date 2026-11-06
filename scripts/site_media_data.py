"""Données de la vidéo et des captures du site (scripts/site_media.ps1) : les communes de la Haute-Garonne, avec leur
population, de l'API Géo d'Etalab (Licence Ouverte), écrites en GeoParquet par le moteur.

Usage : python scripts/site_media_data.py <fichier.parquet>
"""

from __future__ import annotations

import sys
import tempfile
import urllib.request
from pathlib import Path

import pyarrow as pa
from etl_engine import formats

URL = "https://geo.api.gouv.fr/departements/31/communes?fields=nom,code,population&format=geojson&geometry=contour"


def main(target: str) -> None:
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "communes.geojson"
        with urllib.request.urlopen(URL, timeout=120) as response:  # proxy du poste (HTTPS_PROXY)
            source.write_bytes(response.read())
        table = pa.concat_tables(formats.read_batches("geojson", str(source), 100_000))
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    writer = formats.open_writer("parquet", target)
    writer.write(table)
    writer.close()
    print(f"{table.num_rows} communes -> {target}")


if __name__ == "__main__":
    main(sys.argv[1])
