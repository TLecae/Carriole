# ADR 0034 — Carte de l'inspecteur par morceaux et niveaux de zoom ; moteur surveillé et relancé

- **Statut** : accepté
- **Date** : 2026-10-13
- **Complète** : ADR 0010 (inspecteur), ADR 0030 (N premières entités)

## Contexte
Deux retours d'usage le même jour :

1. Un port de 24 entités (parcelles RPG fusionnées sur l'Occitanie, 485 Mo) fait ramer l'inspecteur. Une seule
   entité compte 10,7 millions de sommets en 313 000 morceaux. Chaque tuile relisait les entités entières de son
   emprise, puis les simplifiait d'un bloc : GEOS met **668 s** à simplifier ce multipolygone (contre 1,5 s pour
   l'éclater en morceaux). Une vingtaine de tuiles demandées en même temps saturaient le processus de l'API (mémoire,
   cœurs, et les six connexions que le navigateur ouvre vers une même adresse).
2. Le moteur s'est arrêté sans rien dire pendant un run : toutes les requêtes échouaient (« Failed to fetch »),
   l'enregistrement compris, et rien n'en gardait la trace (le moteur tourne sans console).

## Décision
**Carte**
1. La vue cartographique d'un port (`etl_engine.view`, version 2) a une ligne par **morceau** (polygone d'un
   multipolygone, ligne, point) et par **niveau** :
   - `FULL` : les morceaux tels quels (zooms 12 et plus) ; `POINTS` : les points, à tous les zooms ;
   - un niveau tous les deux zooms (0, 2… 10) : les morceaux d'au moins un pixel sont simplifiés au pixel du zoom
     suivant ; les petits polygones deviennent des **cellules d'un pixel**, fusionnées par rangées.
   Tri par niveau puis Hilbert : une tuile ne lit que les morceaux de son niveau dans son emprise.
2. Les attributs ne sont plus copiés dans la vue : la table lit le port. Filtres et couleur de la carte sont joints
   par `fid`.
3. Un fichier par entité (`.ent.parquet`, trié par `fid`) donne le type de géométrie (colonne de la table) et le
   nombre de sommets, sans lire les géométries.
4. **Sélection** d'une entité de plus de 100 000 sommets : géométrie lue dans un niveau généralisé, environ
   1 000 pixels sur son emprise, au dix-centimètre près.
5. Au plus *cœurs / 2* tuiles calculées en même temps ; chaque tuile garde un seul carré par pixel.
6. **Au plus 50 000 entités dessinées dans la vue** (demande de l'utilisateur) : la carte compte les entités de sa
   vue à chaque déplacement (`/in-view`, emprises du fichier par entité, filtres compris). Au-delà, les tuiles ne
   gardent qu'une entité sur `every` : paliers 1, 2, 3, 4, 6, 8…, soit 33 000 à 50 000 dessinées. L'échantillon
   dépend du seul `fid`, il est donc le même d'une tuile et d'un zoom à l'autre. Un message invite à zoomer pour tout
   voir. Sur 1,6 M de parcelles RPG en vue régionale : une sur 48 ; le comptage prend 1,7 s.

**Moteur**
7. `logs/server.log` (démarrage, arrêt, workers relancés, erreurs des requêtes), `logs/server-crash.log` (pile d'un
   plantage natif, `faulthandler`), `logs/server-console.log` (sortie console). Journaux des workers en UTF-8, avec
   la pile d'un plantage natif.
8. `/api/health` répond sur la boucle d'événements, même quand toutes les requêtes lourdes sont occupées.
9. L'application de bureau interroge le moteur toutes les 5 s. Elle le relance s'il est arrêté, ou s'il ne répond
   plus depuis 90 s (arrêt forcé avant). La relance garde **la même adresse et le même jeton** (`--port`,
   `ETL_SERVE_TOKEN`) : la page continue sans se recharger. Si l'adresse est prise, la page est rechargée.
10. La page détecte une requête sans réponse : bandeau « reconnexion », sondage de `/api/health` toutes les 2 s, puis
    « reconnecté ». Un enregistrement demandé pendant la coupure est refait au retour du moteur.

## Conséquences
Sur le port RPG fusionné :

| Mesure | Avant | Après |
| --- | --- | --- |
| Vue (première ouverture) | échec | 66 s, une fois |
| Une tuile, du zoom 5 au zoom 14 | plusieurs minutes | 0,1 à 0,5 s, au plus 400 Ko |
| Sélection d'une entité | 4,7 s, 40 Mo de GeoJSON | 0,4 s, 0,7 Mo |

- La vue d'un port polygonal est un peu plus grosse que le port (niveaux en plus, attributs en moins).
- Un run en cours quand le moteur est relancé passe « interrompu » : *Reprendre* le continue depuis son cache.
