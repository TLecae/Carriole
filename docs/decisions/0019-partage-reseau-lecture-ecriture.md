# ADR 0019 — Écriture en deux temps sur un partage réseau

- **Statut** : accepté
- **Date** : 2026-10-08
- **Complète** : [ADR 0016](0016-dossiers-autorises.md), [ADR 0020](0020-application-de-bureau.md)

## Contexte
Les flux lisent et écrivent sur le partage de l'équipe (`Z:\…`, `\serveur\partage\…`). Deux défaillances sont propres
aux partages :
1. **Une écriture est interrompue** (erreur, annulation, liaison coupée) : le fichier de sortie reste à moitié écrit
   sur le partage, et des collègues l'ouvrent.
2. **SQLite sur SMB** : un GeoPackage écrit directement sur un partage est très lent. Mesuré le 2026-10-08 sur un
   partage SMB de test : 5 000 points en 5,1 s en direct contre moins de 0,1 s en deux temps ; 200 000 points
   en direct n'étaient pas terminés après neuf minutes (fichier à 0 octet), en deux temps quelques secondes.

## Décision
**Écriture en deux temps** (`ETL_STAGED_ROOTS`, `etl_engine.staging`) pour les dossiers listés : le `Writer` écrit dans
`<cache>/_staging/<id>/`, puis publie chaque fichier par copie sous un nom caché du dossier de destination
(`.nom.xxxx.part`), `fsync`, `os.replace`. Un fichier existant que le mode conserve (ajout, couche de GeoPackage,
feuille Excel) est d'abord copié en local. La destination n'est touchée qu'à la publication ; en cas d'échec le travail
local et le `.part` sont supprimés. Pour un format à fichiers multiples (Shapefile, MapInfo) seules les extensions du
format sont copiées et supprimées : un `donnees.csv` voisin n'est jamais touché.

- Les dossiers de travail de plus d'un jour sont purgés avec le cache (`_staging`, comme `_exports`).
- Les **accès en lecture** restent directs sur le partage.
- Tests : `engine/tests/test_staging.py`, `store/tests/test_purge.py`.

## Conséquences et limites
- Un fichier **ouvert par un collègue** peut refuser d'être remplacé (violation de partage) : la publication échoue
  avec le message du système, le fichier existant reste intact.
- La publication de plusieurs fichiers (Shapefile) n'est pas atomique en bloc. Deux flux qui modifient le même
  GeoPackage en même temps s'écrasent (le dernier publié l'emporte) : il n'y a pas de verrou entre flux.
- Un dossier GeoParquet partitionné est écrit directement (pas de copie d'un dossier entier).
- Un worker tué brutalement peut laisser un `.part` caché sur le partage : à supprimer à la main.

## Alternatives écartées
- **SMB natif dans l'application** : exige une copie locale de chaque fichier lu (GDAL ne lit pas SMB) et une
  abstraction de tous les chemins ; inutile, Windows donne déjà l'accès aux partages.
- **Écrire directement sur le partage** : rapide à réaliser, mais cumule les deux défaillances ci-dessus.
