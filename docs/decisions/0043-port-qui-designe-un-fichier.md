# ADR 0043 — Port qui désigne un fichier

- **Statut** : accepté
- **Date** : 2026-10-29
- **Complète** : ADR 0003 (ports en Parquet), 0013 (voie SQL), 0042 (lecture par DuckDB)

## Contexte
Un fichier Parquet de 4,2 Go (13,6 M de lignes, 226 colonnes, presque toutes du texte) mettait plus de 20 minutes
à se lire, cache désactivé ou non. Le lecteur recopiait tout le fichier dans le port : environ 32 Go à décompresser
puis à recompresser. DuckDB seul met 47 s par million de lignes, soit environ 10 minutes pour ce fichier ; sans
l'ordre des lignes, 7 minutes. Le format n'y est pour rien : les ports sont eux-mêmes des fichiers Parquet. Ce
fichier en était déjà un.

## Décision
1. **Un modificateur peut déclarer que ses ports existent déjà** (`Modifier.ready_ports`) : un fichier Parquet au
   format des ports, c'est-à-dire sans géométrie, ou avec sa géométrie en WKB dans `geometry` et un SCR lisible par
   `get_crs`. Le moteur écrit alors `<port>.ref.json` (chemin, taille, date du fichier) au lieu de `<port>.parquet`.
   C'est une voie directe, comme la voie SQL : le port est complet d'un coup, aucun consommateur n'est branché en
   direct dessus, et `ETL_SQL_ROUTE=0` revient à la lecture par lots.
2. **Le lecteur (Geo)Parquet** la déclare pour un seul fichier local, sans *Ajouter le nom du fichier* ni *Lignes lues
   au plus*, déjà au format des ports. Dans les autres cas, il recopie comme avant : plusieurs fichiers, adresse web,
   géométrie sous un autre nom ou dans un autre encodage. Le CSV n'est pas concerné : un texte doit être converti une
   fois, sinon chaque nœud suivant et chaque tri de l'inspecteur le relirait en entier.
3. **Une seule résolution** : `cache.port_data(port)` donne le fichier à lire, celui du port ou celui qu'il désigne.
   Le moteur (entrées des nœuds, reprise du cache, composants), l'inspecteur et la vue cartographique passent par
   elle. Les fichiers dérivés (vue cartographique) restent rangés dans le run, jamais à côté du fichier désigné.
   Supprimer un port (run sans cache, purge) ne supprime que sa référence.
4. **Fichier modifié depuis le run** : `port_data` lève `SourceChanged` (« relancer le flux »). L'empreinte du
   lecteur (date et taille du fichier) change aussi : le run suivant relit le fichier.
5. **SCR GeoParquet** : `get_crs` lit `etl.crs`, sinon le SCR de `geometry` dans les métadonnées « geo ». Les lots
   d'un port ne portent que son SCR (`PortSource`), qu'il ait été écrit ou désigné.
6. **Inspecteur** : sa connexion reste limitée au dossier du cache. Un port qui désigne un fichier a sa propre
   connexion, qui autorise en plus ce seul fichier (`allowed_paths`). Le SQL libre ne lit toujours rien d'autre.
7. **Run sans cache : un port que rien ne lit n'est pas écrit**, sur un nœud dont d'autres ports sont lus (`Failed`
   d'un filtre non relié, ports non joints). Il est compté : par `PortSink` sans fichier (voie Python), par
   `SELECT count(*)` (voie SQL). Ses données n'étaient de toute façon pas gardées : sans cache, les fichiers d'un nœud
   partent dès que ses consommateurs ont fini. Un nœud sans consommateur écrit tous ses ports, qui sont les résultats
   du run. Depuis l'[ADR 0044](0044-ports-calcules-a-la-demande.md), sur la voie SQL, un port que rien ne lit est
   défini par sa requête, avec ou sans cache : il est compté, et calculé seulement si on l'ouvre.

## Mesures
Fichier ci-dessus, poste de développement (Windows, 12 cœurs logiques, 16 Go) :

| | Avant | Après |
| --- | ---: | ---: |
| Lecture (nœud seul) | plus de 20 min (annulée) | 0,1 s |
| Ouverture dans l'inspecteur | — | 0,08 s |
| Filtre dans l'inspecteur (`count(*)` avec condition) | — | 0,17 s |
| *Filtrer par expression* en aval, sans cache (13,1 M lignes dans `Failed`, non relié) | 250 s | 44 s |

## Conséquences
- Un fichier déplacé, modifié ou supprimé après le run ne peut plus être consulté dans ce run : on relance le flux.
- Sur un lecteur réseau, l'inspecteur et les nœuds suivants lisent le fichier à travers le réseau.
- Avec cache, un nœud en aval écrit tous ses ports, reliés ou non : ils restent consultables et repris au run
  suivant.
- *Filtrer par expression* n'avait pas de voie SQL : les 13,6 M lignes passaient par Python (44 s). Réglé par
  l'[ADR 0044](0044-ports-calcules-a-la-demande.md) (ports calculés à la demande) : 2 s.
