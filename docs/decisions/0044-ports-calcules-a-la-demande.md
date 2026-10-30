# ADR 0044 — Ports calculés à la demande

- **Statut** : accepté
- **Date** : 2026-10-29
- **Complète** : ADR 0013 (voies d'exécution ; revient sur son §3), 0042 (lecture par DuckDB), 0043 (port qui désigne
  un fichier)

## Contexte
Chaque nœud écrivait toutes les colonnes de toutes ses sorties dans un fichier Parquet, pour le nœud suivant et pour
l'inspecteur. Le coût d'un flux suivait donc le volume × le nombre de nœuds. Sur un fichier de 13,6 M lignes ×
226 colonnes, un simple filtre coûtait 44 s, alors qu'il n'évalue qu'une colonne ; compter ce qu'il garde et ce qu'il
rejette prend 0,1 s. Un script DuckDB est rapide parce qu'il compose toute la chaîne en une requête, ne lit que les
colonnes utiles et n'écrit rien d'intermédiaire. L'ADR 0013 (§3) avait écarté la fusion de nœuds pour garder chaque
résultat consultable dans l'inspecteur.

## Décision
1. **Un port a trois formes** (`cache.py`) : son fichier (`<port>.parquet`), la référence à un fichier existant
   (`<port>.ref.json`, ADR 0043), ou sa **définition** (`<port>.sql.json`) : une requête DuckDB sur des ports de
   l'amont, désignés par (nœud, port). `port_relation` donne la requête qui lit n'importe quel port (composition
   récursive) ; `materialize` en écrit le fichier.
2. **Qui est différé** : tout port de la voie SQL d'un nœud qui a des entrées (pas un lecteur). Exception : le port
   d'un modificateur qui doit tout charger (tri, statistiques, jointure), quand un nœud le lit, est écrit, pour ne pas
   refaire le calcul à chaque lecture (`executor.written`).
   - Un modificateur qui traite les lignes une à une (`mode = "streaming"`) n'écrit donc aucun port.
   - Un modificateur bloquant n'écrit que les ports qu'un nœud lit, et lit ses entrées sous forme de requêtes
     composées.
   - Une expression non déterministe (`random()`, `now()`…) empêche toute définition, puisque relue elle changerait.
     Le nœud ligne à ligne passe alors par la voie par lots ; le nœud bloquant écrit ses ports comme avant.

   Écrire un port et le définir passent par une seule fonction, port par port (`sqlroute.run_sql_route`).
3. **Contrôle et comptes** : avant d'enregistrer les définitions, le moteur évalue chaque requête sur toutes les lignes
   (`count(*)`, plus `count(colonne)` pour les colonnes calculées déclarées par `sql_checked`). Une erreur sur une
   donnée (conversion…) renvoie le nœud vers la voie par lots, qui rejette exactement les entités en cause. Des types
   autres que ceux prévus par `output_schema` aussi.
4. **`TRY` dans les définitions** : les expressions d'utilisateur (marquées par `expr.guarded`) y sont enveloppées
   dans `TRY(…)`. Le contrôle a montré qu'aucune ligne qui atteint le nœud n'échoue ; mais DuckDB peut, dans une chaîne
   composée, évaluer une condition avant le filtre amont, sur des lignes que ce filtre écarte : `TRY` y rend NULL, et
   ces lignes sont écartées de toute façon.
5. **Même vue des lignes que la voie par lots** (`expr.sql_base`, `expr.sql_columns`) : géométrie en `GEOMETRY` pour
   les expressions, WKB d'origine gardé à sa place ; une condition NULL compte comme fausse.
6. **Consommateurs** : un nœud SQL compose les requêtes ; un nœud traité par lots (géométrie, écriture, script) lit la
   requête composée par lots Arrow, sans fichier intermédiaire (`PortSource`) ; chaque consommateur la relance.
7. **Inspecteur** : à la première ouverture d'un port défini, `materialize` écrit son fichier en arrière-plan
   (`PREPARING`, 425 : l'interface affiche « Préparation des données… » et redemande) ; ensuite tout fonctionne comme
   avant (fid, pages, carte, export), et le fichier reste avec le run.

   Choisir un nœud ouvre sa première sortie, qui peut être longue à calculer (par exemple les entités avec leurs
   statistiques). Un calcul que l'interface ne redemande plus depuis 5 s (autre port, autre nœud) est donc arrêté
   (`duck.interrupted_when`), sans rien écrire. Il reprend si l'on revient à ce port.
8. **Sécurité** : les connexions qui évaluent ces requêtes sont confinées après le chargement des extensions
   (`duck.confine`) : elles ne lisent que les fichiers des ports concernés et n'écrivent que dans le run. Une
   expression d'utilisateur n'atteint rien d'autre, comme dans `expr`.
9. **Cache, nettoyage** : une définition reprise désigne les ports du nouveau run (liens, sans réécriture). Sans cache,
   les fichiers d'un nœud restent tant qu'une définition gardée les désigne (un nœud final différé garde sa chaîne).
   Exception : le port d'un modificateur qui doit tout charger, seulement défini parce que rien ne le lisait, n'est pas
   repris quand un nœud le lit dans le nouveau run ; le nœud est recalculé et le port écrit une fois
   (`executor.previous_result`). Sinon chaque lecture refaisait tout le calcul : des statistiques rattachées aux
   entités de 13,6 M lignes × 226 colonnes, relues par un nœud par lots, ont rempli 33 Go de débordement sur disque.
10. **Couverts** : `ExtractByExpression`, `RouteByExpression`, `AddAttributes`, `CastAttributes`,
    `RefactorAttributes`, `DropAttributes`, `RetainAttributes`, `RenameAttributes`, `RenameAttributesByPattern`.
    Chacun est comparé à la voie par lots par un test (colonnes, types, ordre, SCR, rejets). Les modificateurs
    géométriques restent sur GEOS.

11. **Tables larges** (`duck.fit`, toute connexion qui lit des ports) : chaque thread de DuckDB garde un bloc lu
    décompressé, et `COPY` un bloc de 122 880 lignes par colonne ; au-delà de sa mémoire, DuckDB déborde sur disque et
    devient dix fois plus lent. Le nombre de threads est borné à mémoire / (4 × plus gros bloc lu), et un bloc écrit
    ne dépasse pas 32 Mo décompressés (`ROW_GROUP_SIZE` selon la largeur moyenne d'une ligne), d'après les métadonnées
    Parquet. Sur des données étroites, rien ne change (12 threads, blocs par défaut). Les lots lus par la voie par lots
    sont bornés de même à 64 Mo.

## Mesures
Banc `bench/run.py 1000000 <cas>`, sans cache, budget du banc (3 Go : 921 Mo par connexion lourde), poste de
développement (Windows, 12 cœurs logiques, 16 Go). Nouveaux cas `large_filtre` (filtre seul, `Failed` non relié) et
`large_chaine` (filtre, attributs, tri), sur une table générée de 1 M lignes × 200 colonnes (texte surtout).

| Cas | Avant (s) | Après (s) |
| --- | ---: | ---: |
| filtre (points) | 4,2 | 0,9 |
| calcul (points) | 3,0 | 2,4 |
| tri (points) | 2,7 | 2,9 à 3,3 |
| statistiques (points) | 3,0 | 3,3 à 3,7 |
| chaîne de 5 nœuds (points) | 6,8 | 5,9 à 6,9 |
| large_filtre | manque de mémoire | 9,9 à 10,9 (pic 1,3 Go) |
| large_filtre, budget 8 Go | 16,5 (pic 6,6 Go) | 5,7 (pic 2,0 Go) |
| large_chaine, budget 8 Go | 25,1 (pic 7,5 Go) | 18,7 (pic 2,5 Go) |

`COPY` d'un résultat large (143 000 lignes × 200 colonnes) avec 921 Mo : 104 s en blocs de 122 880 lignes, 9,3 s en
blocs de 20 000. Le tri de ces lignes demande davantage : il échoue à 921 Mo et passe à 3,6 Go (le budget par défaut
d'un poste de 16 Go) en 6,9 s.

Fichier réel (13,6 M lignes × 226 colonnes, 4,2 Go, lu sur un lecteur réseau, blocs de 468 Mo décompressés), budget
par défaut de l'application :

| | Avant | Après |
| --- | ---: | ---: |
| Lecture + filtre, sans cache | 44 s (filtre, copie locale) | 2 s |
| Lecture + filtre, avec cache | — | 8,3 s |
| Lecture, filtre, tri des 492 000 lignes gardées, écriture | 67 s (copie locale) | 102 s (réseau ; tri 91 s) |
| Inspecteur, première ouverture de `Passed` (492 000 lignes) | immédiate (écrit pendant le run) | 66 s |
| Inspecteur, première ouverture de `Failed` (13,1 M lignes, 4,35 Go) | — | 301 s |
| Lecture, statistiques par commune, sans cache ; seul le résumé sert | plus de 10 min (sortie `output` écrite) | 8 s |
| Inspecteur, première ouverture du résumé (35 248 communes) | immédiate | 1,1 s |

Avec des blocs de 468 Mo, la règle du point 11 ne laisse qu'un thread : le tri et la préparation lisent tout le
fichier à travers le réseau sur un seul cœur. C'est la limite suivante.

Pour les statistiques, aucun nœud ne lisait la sortie `output` : les 13,6 M entités, avec les statistiques de leur
commune, dans leur ordre. Son écriture occupait pourtant tout le run.

Quand un nœud la lit (ou l'inspecteur), les statistiques de chaque entité sont désormais calculées sur les seules clés
de groupe, triées par numéro de ligne, puis recollées par position (`POSITIONAL JOIN`) aux entités lues en flux, au lieu
de trier les entités elles-mêmes avec toutes leurs colonnes. Banc `large_stats` (1 M lignes × 200 colonnes, budget du
banc 3 Go, sortie écrite) : manque de mémoire avant, 94 s (pic 1,3 Go) après. Essai isolé (1,5 M lignes × 60 colonnes,
900 Mo) : 292 s et 5,5 Go de débordement avant, 56 s sans débordement après, ordre identique.

## Conséquences
- Ouvrir un port différé dans l'inspecteur coûte son calcul, une fois. Pour la sortie non lue d'un tri ou de
  statistiques, c'est tout le calcul du nœud.
- Avec cache, un port non lu reste défini. Un nœud ajouté plus tard pour le lire refait le calcul tant que le nœud
  qui le définit est repris du cache, une fois par nœud qui le lit.
- Un port différé n'est plus lu « en direct » par son consommateur (ADR 0013 §6) : il n'y a plus rien à attendre.
- Trier ou regrouper des lignes très larges demande toujours de la mémoire : quelques centaines de milliers de lignes
  de 200 colonnes de texte dépassent 1 Go par connexion lourde.
