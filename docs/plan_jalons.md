# Plan des jalons

**Objectif** : des modificateurs Python qu'on branche les uns aux autres dans un éditeur
graphique, confortable pour construire et déboguer un flux. Le catalogue réel est dans
[catalogue.md](catalogue.md).

## Catalogue : ordre d'implémentation

| Lot | Modificateurs |
| --- | --- |
| J1 (5 premiers) | AddAttributes, RefactorAttributes, ExtractByExpression (+ RouteByExpression), RenameAttributes, RetainAttributes |
| J6 lot A (fait) | StatisticsByGroup, JoinByField / LookupJoin, DropAttributes, Counter, OrderBy, FindDuplicates, Reproject, DatabaseQuery / DatabaseQueryPerFeature |
| J6 lot B (fait) | JoinByLocation, ExtractByLocation, Aggregate, MapValues, ReplaceNulls, PointFromXY, ExplodeList, SplitText, MeasureArea, GeometryToAttribute |
| J6 lot C (fait) | Buffer, Clip, Dissolve, PointsInPolygons, CheckValidity, ExtractCoordinates, Centroids… |

Reader/Writer par format arrivent aux jalons 1 (GeoParquet, GeoPackage, CSV) et 5 (PostgreSQL,
Shapefile, MapInfo, Excel).

## Jalons

| # | Contenu | Critère de fin |
| --- | --- | --- |
| 0 | Dépôt, inventaire, ADR, CI locale | CI locale verte |
| 1 | Moteur, SDK, CLI, cache GeoParquet, 5 modificateurs, formats GeoParquet/GPKG/CSV, bench 1 M | tests + bench consigné |
| 2 | Éditeur minimal : canvas, palette, formulaires générés, sauvegarde, run, comptes, logs | parcours Playwright |
| 3 | Inspecteur : table filtrable côté serveur, carte en tuiles DuckDB, synchro | tests + mesure tuiles |
| 4 | Runs en arrière-plan : workers, liste, reconnexion, annulation, relance depuis le cache | test d'intégration (worker tué, interruption, reprise) |
| 5 | Formats : PostgreSQL/PostGIS, Shapefile, MapInfo, Excel multi-onglets | aller-retour par format |
| 6 | Catalogue de modificateurs (lots A à C) | tests unitaires par transformer |
| 7 | Performance : parallélisme par lots et branches, voie SQL, 10 M | objectifs de bench atteints |
| 8 | Composants (sous-flux), planification, durcissement | CI complète |
| 9 | Application de bureau Windows : backend local sécurisé, flux et composants en fichiers, CLI (`serve`, `inspect`, `runs`, `config`, `connections`), CI Windows | `scripts/ci.ps1` vert, bench 1 M identique ([ADR 0020](decisions/0020-application-de-bureau.md), [0021](decisions/0021-flux-et-composants-en-fichiers.md)) |
| 10 | Fenêtre Electron, installeur par utilisateur (sans droits administrateur), runtime Python embarqué, `etl` dans le PATH ([ADR 0022](decisions/0022-electron-et-installeur.md)) | installation sans élévation, flux exécuté hors ligne par l'installation |
| 11 | Internationalisation : français conservé, anglais complet, langue choisie à l'installation et dans les paramètres ([ADR 0023](decisions/0023-internationalisation.md)) | `etl i18n check` vert, interface 100 % anglaise (**fait**) |
| 12 | Variables (`${sys:…}`, `${var:…}`), dossiers et listes de fichiers, traitement par lots (`--each`), `RunFlow` ([ADR 0024](decisions/0024-variables-et-lots.md)) | `--each` sur un dossier de fichiers (**fait**) |
| 13 | 21 modificateurs tabulaires et vecteur (47 → 68) ([ADR 0025](decisions/0025-ports-dynamiques-et-code-en-ligne.md)) | tests unitaires par transformer (**fait**) |
| 14 | Rasters : fichiers, STAC, calculs, mosaïque, zonal (17 modificateurs : 68 → 85) ([ADR 0026](decisions/0026-rasters.md)) | flux Sentinel-2 NDVI + flux MNT (**fait**) |
| 15 | Assistant IA (Anthropic, OpenAI, OpenRouter, Ollama) : créer, déboguer, écrire un transformer ([ADR 0027](decisions/0027-assistant-ia.md)) | faux fournisseur en CI (**fait**), essai manuel sur les vrais fournisseurs (à faire) |
| 16 | Nettoyage de l'ancienne version serveur, barre du haut en menus, fenêtre Paramètres, installeur à assistant (administrateur, `.exe` seul) ([ADR 0022](decisions/0022-electron-et-installeur.md)) | CI locale verte, installeur construit (**fait**) |

## Objectifs de benchmark proposés (jeu synthétique, machine de dev)

| Cas | 1 M entités | 10 M entités |
| --- | --- | --- |
| Lecture GeoParquet → filtre attributaire → écriture | < 10 s | < 90 s |
| Calcul d'attribut (SQL) + renommage | < 8 s | < 70 s |
| Reprojection de points | < 20 s | < 200 s |
| Mémoire crête d'un run en streaming | < 1,5 Go | < 3 Go |

Mesures réelles : voir [journal_jalons.md](journal_jalons.md) (jalons 1 et 7) ; objectifs à 10 M atteints.

## Questions ouvertes
Voir [questions_ouvertes.md](questions_ouvertes.md).
