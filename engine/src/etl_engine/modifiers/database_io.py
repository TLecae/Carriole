"""Lecture et écriture Oracle (PostgreSQL : `postgres.py`), un lecteur et un écrivain par base.

La mécanique est commune (`formats/dbapi.py`) : chaque base n'apporte que son type de connexion et ses textes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, ClassVar, Literal

import pyarrow as pa
from pydantic import Field

from etl_engine.formats import databases
from etl_engine.sdk import (
    AttributeList,
    ConfigError,
    Crs,
    DbConnection,
    Emit,
    Modifier,
    Params,
    PgTable,
    PgTableOut,
    db_connection,
    first_rows,
    row_limit,
)

OracleConnection = Annotated[str, db_connection("oracle")]


class DatabaseReader(Modifier):
    """Lecteur d'une base (classe de base : sans nom, elle n'est pas dans la palette)."""

    reusable = "partial"  # relu à chaque run, repris dans un run « jusqu'au nœud » (Modifier.reusable)
    category = "Lecture"
    inputs = ()
    outputs = ("output",)
    rejected = False
    kind: ClassVar[str] = ""

    def _check(self) -> None:
        p = self.params
        if not p.connection:
            raise ConfigError("connexion non renseignée")
        if not (p.table.strip() or p.query.strip()):
            raise ConfigError("indiquer une table ou une requête")

    def _opts(self) -> dict[str, Any]:
        p = self.params
        return {"table": p.table, "query": p.query, "geometry_column": p.geometry_column, "crs": p.crs or None}

    def _db(self) -> Any:
        if databases.kind_of(self.params.connection) != self.kind:
            raise ConfigError(f"la connexion « {self.params.connection} » n'est pas du bon type de base")
        return databases.backend(self.params.connection)

    def output_schema(self, inputs: Mapping[str, pa.Schema | None]) -> dict[str, pa.Schema | None]:
        self._check()
        try:
            return {"output": self._db().read_schema(self.params.connection, **self._opts())}
        except ConfigError as exc:
            if "introuvable" in str(exc) or "invalide" in str(exc) or "type de base" in str(exc):
                raise
            return {"output": None}  # base injoignable au moment de l'édition : le run dira pourquoi
        except Exception:  # noqa: BLE001
            return {"output": None}

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        self._check()
        total = 0
        db = self._db()
        batches = db.read_batches(self.params.connection, self.ctx.batch_rows, where=self.params.where, **self._opts())
        for batch in first_rows(batches, self.params.max_rows):
            self.ctx.check_cancel()
            total += batch.num_rows
            self.ctx.progress(total)
            emit("output", batch)
        self.ctx.info(f"{total} entités lues")


class DatabaseWriter(Modifier):
    """Écrivain d'une base (classe de base)."""

    category = "Écriture"
    inputs = ("input",)
    outputs = ()
    rejected = False
    stateful = True
    kind: ClassVar[str] = ""

    def run(self, inputs: Mapping[str, Any], emit: Emit) -> None:
        p = self.params
        if not p.connection:
            raise ConfigError("connexion non renseignée")
        if not p.table.strip():
            raise ConfigError("table non renseignée")
        if databases.kind_of(p.connection) != self.kind:
            raise ConfigError(f"la connexion « {p.connection} » n'est pas du bon type de base")
        writer = databases.backend(p.connection).writer(
            p.connection,
            p.table,
            mode=p.mode,
            key=list(p.key),
            geometry_column=p.geometry_column or "geom",
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


MODES = (
    "create : échoue si la table existe ; overwrite : la recrée ; append : ajoute ; truncate : vide puis insère ; "
    "upsert : met à jour les lignes de même clé et insère les autres."
)


class ReaderParams(Params):
    connection: DbConnection = Field("", title="Connexion", description="Connexion nommée (menu Connexions).")
    table: PgTable = Field(
        "",
        title="Table ou vue",
        description="Table ou vue lue en entier. Ignorée si une requête est renseignée.",
        examples=["carto.communes"],
    )
    query: str = Field(
        "",
        title="Requête SQL",
        description="Facultatif : remplace la table ; un seul SELECT.",
        examples=["SELECT id, nom, geom FROM carto.communes WHERE dep = '31'"],
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


class WriterParams(Params):
    connection: DbConnection = Field("", title="Connexion", description="Connexion nommée (menu Connexions).")
    table: PgTableOut = Field(
        "", title="Table", description="Schéma puis table (existante, ou nouvelle à créer : taper son nom)."
    )
    mode: Literal["create", "overwrite", "append", "truncate", "upsert"] = Field(
        "append", title="Mode", description=MODES
    )
    key: AttributeList = Field(
        default_factory=list, title="Clé (upsert / clé primaire)", description="Colonnes identifiant une ligne."
    )
    geometry_column: str = Field(
        "geom",
        title="Colonne géométrique créée",
        description="Nom de la colonne géométrique, utilisé quand la table est créée.",
    )
    create_spatial_index: bool = Field(True, title="Index spatial", description="Crée un index spatial avec la table.")
    crs: Crs = Field("", title="Système de coordonnées", description="Vide = celui des données.")


CONNECTION = {"title": "Connexion", "description": "Connexion nommée (menu Connexions)."}


class OracleReader(DatabaseReader):
    name = "OracleReader"
    label = "Lecture Oracle"
    description = "Lit une table, une vue ou une requête Oracle ; la colonne SDO_GEOMETRY devient la géométrie du flux."
    help = (
        "Choisir une table (schéma puis table) **ou** écrire une requête SQL. Le filtre est ajouté en WHERE : il est "
        "exécuté par le serveur. Le système de coordonnées est le SRID des géométries (SDO_SRID). Oracle range en "
        "majuscules les noms saisis sans guillemets : les colonnes lues sont en majuscules (NOM, GEOM)."
    )
    kind = "oracle"

    class Params(ReaderParams):
        connection: OracleConnection = Field("", **CONNECTION)


class OracleWriter(DatabaseWriter):
    name = "OracleWriter"
    label = "Écriture Oracle"
    description = "Écrit les entités dans une table Oracle (créée si besoin), en une seule transaction."
    help = (
        "Modes : " + MODES + " Les lignes sont écrites dans une transaction ; mais Oracle valide aussitôt la "
        "création et la suppression d'une table (create, overwrite). La géométrie est écrite en SDO_GEOMETRY avec le "
        "SRID des données ; à la création, les métadonnées (USER_SDO_GEOM_METADATA) et l'index spatial sont créés. "
        "Les noms de table et de colonnes créés sont en majuscules."
    )
    kind = "oracle"

    class Params(WriterParams):
        connection: OracleConnection = Field("", **CONNECTION)
