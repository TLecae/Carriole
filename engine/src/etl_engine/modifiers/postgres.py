"""Lecture et écriture PostgreSQL / PostGIS."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal

import pyarrow as pa
from pydantic import Field

from etl_engine.formats import pg
from etl_engine.sdk import (
    AttributeList,
    ConfigError,
    Crs,
    Emit,
    Modifier,
    Params,
    PgTable,
    PgTableOut,
    db_connection,
    first_rows,
    row_limit,
)

PgConnection = Annotated[str, db_connection("postgres")]


class PostgresReader(Modifier):
    name = "PostgresReader"
    reusable = "partial"  # relu à chaque run, repris dans un run « jusqu'au nœud » (Modifier.reusable)
    label = "Lecture PostgreSQL / PostGIS"
    category = "Lecture"
    description = (
        "Lit une table, une vue ou une requête PostgreSQL ; la géométrie PostGIS devient la géométrie du flux."
    )
    help = (
        "Choisir une table (schéma puis table, dans les listes lues sur la connexion) **ou** écrire une requête SQL. Le filtre est ajouté en WHERE : il est exécuté par "
        "le serveur, donc seules les lignes utiles circulent. La lecture se fait en lecture seule, par curseur "
        "serveur (mémoire bornée). Le système de coordonnées est celui de la colonne (SRID). "
        "Les « numeric » sont lus en flottant ; les types non gérés (intervalles, hstore…) en texte."
    )
    inputs = ()
    outputs = ("output",)
    rejected = False

    class Params(Params):
        connection: PgConnection = Field("", title="Connexion", description="Connexion nommée (menu Connexions).")
        table: PgTable = Field(
            "",
            title="Table ou vue",
            description=(
                "Table ou vue à lire en entier (équivaut à SELECT * FROM table). Ignorée si une requête est renseignée."
            ),
            examples=["public.communes"],
        )
        query: str = Field(
            "",
            title="Requête SQL",
            description=(
                "Facultatif. Vide : la table est lue en entier (SELECT * FROM table). Renseignée : remplace la "
                "table ; un seul SELECT (un « ; » final est toléré)."
            ),
            examples=["SELECT id, nom, geom FROM public.communes WHERE dep = '31'"],
        )
        where: str = Field(
            "",
            title="Filtre (WHERE)",
            description="Condition SQL ajoutée en WHERE, évaluée par le serveur ; vide = aucun filtre.",
            examples=["dep = '31' AND pop > 1000"],
        )
        geometry_column: str = Field(
            "", title="Colonne géométrique", description="Vide = la première trouvée.", examples=["geom"]
        )
        crs: Crs = Field("", title="Système de coordonnées", description="Vide = SRID de la colonne.")
        max_rows: int = row_limit()

    def _check(self) -> None:
        p = self.params
        if not p.connection:
            raise ConfigError("connexion non renseignée")
        if not (p.table.strip() or p.query.strip()):
            raise ConfigError("indiquer une table ou une requête")

    def _opts(self) -> dict[str, Any]:
        p = self.params
        return {
            "connection": p.connection,
            "table": p.table,
            "query": p.query,
            "geometry_column": p.geometry_column,
            "crs": p.crs or None,
        }

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        self._check()
        try:
            return {"output": pg.read_schema(**self._opts())}
        except ConfigError as exc:
            if "introuvable" in str(exc) or "invalide" in str(exc):
                raise
            return {"output": None}  # base injoignable au moment de l'édition : le run dira pourquoi
        except Exception:  # noqa: BLE001
            return {"output": None}

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        self._check()
        total = 0
        batches = pg.read_batches(batch_rows=self.ctx.batch_rows, where=self.params.where, **self._opts())
        for batch in first_rows(batches, self.params.max_rows):
            self.ctx.check_cancel()
            total += batch.num_rows
            self.ctx.progress(total)
            emit("output", batch)
        self.ctx.info(f"{total} entités lues")


class PostgresWriter(Modifier):
    name = "PostgresWriter"
    label = "Écriture PostgreSQL / PostGIS"
    category = "Écriture"
    description = "Écrit les entités dans une table PostgreSQL (créée si besoin), en une seule transaction."
    help = (
        "Modes : « create » échoue si la table existe ; « overwrite » la recrée ; « append » ajoute ; « truncate » "
        "la vide puis insère ; « upsert » met à jour les lignes de même clé et insère les autres. "
        "Tout est écrit dans une transaction : en cas d'erreur, la table reste comme avant. "
        "Les colonnes des données doivent exister dans la table (hors création). Un index spatial GIST est créé "
        "avec la table. Avec « auto », polygones et lignes sont créés en « Multi » (les géométries simples sont "
        "promues) ; sinon le type du premier lot : le fixer explicitement si les données sont hétérogènes."
    )
    inputs = ("input",)
    outputs = ()
    rejected = False
    stateful = True

    class Params(Params):
        connection: PgConnection = Field("", title="Connexion", description="Connexion nommée (menu Connexions).")
        table: PgTableOut = Field(
            "", title="Table", description="Schéma puis table (existante, ou nouvelle à créer : taper son nom)."
        )
        mode: Literal["create", "overwrite", "append", "truncate", "upsert"] = Field(
            "append",
            title="Mode",
            description=(
                "create : échoue si la table existe ; overwrite : la recrée ; append : ajoute ; truncate : vide "
                "puis insère ; upsert : met à jour les lignes de même clé et insère les autres."
            ),
        )
        key: AttributeList = Field(
            default_factory=list, title="Clé (upsert / clé primaire)", description="Colonnes identifiant une ligne."
        )
        geometry_column: str = Field(
            "geom",
            title="Colonne géométrique créée",
            description="Nom de la colonne géométrique, utilisé quand la table est créée.",
        )
        geometry_type: Literal[
            "auto", "Geometry", "Point", "LineString", "Polygon", "MultiPoint", "MultiLineString", "MultiPolygon"
        ] = Field(
            "auto",
            title="Type de géométrie (création)",
            description=(
                "Type de la colonne créée. auto : multi-surfaces et multi-lignes pour les polygones et les "
                "lignes, sinon le type du premier lot."
            ),
        )
        create_spatial_index: bool = Field(
            True, title="Index spatial (GIST)", description="Crée un index spatial GIST avec la table."
        )
        crs: Crs = Field("", title="Système de coordonnées", description="Vide = celui des données.")

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        p = self.params
        if not p.connection:
            raise ConfigError("connexion non renseignée")
        if not p.table.strip():
            raise ConfigError("table non renseignée")
        writer = pg.PgWriter(
            p.connection,
            p.table,
            mode=p.mode,
            key=list(p.key),
            geometry_column=p.geometry_column or "geom",
            geometry_type=p.geometry_type,
            create_spatial_index=p.create_spatial_index,
            crs=p.crs or None,
        )
        rows = 0
        try:
            for batch in inputs.get("input", ()):
                self.ctx.check_cancel()
                writer.write(batch)
                rows += batch.num_rows
        except BaseException:
            writer.abort()
            raise
        writer.close()
        for message in writer.messages:
            self.ctx.info(message)
        if rows == 0:
            self.ctx.warning("aucune entité reçue : rien n'a été écrit")
        else:
            self.ctx.info(f"{rows} entités écrites dans {p.table} (mode {p.mode})")
