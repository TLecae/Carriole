# ADR 0028 — Performance, mémoire et runs parallèles

- **Statut** : accepté
- **Date** : 2026-10-12
- **Complète** : [ADR 0013](0013-voies-execution-et-parallelisme.md) ; **remplace** la vue d'inspection et les tuiles
  vectorielles de l'[ADR 0010](0010-cache-geoparquet-et-tuiles.md)

## Contexte
Sur le poste, le parallélisme ne se ressentait pas, un traitement géospatial s'est arrêté en « out of memory »,
et plusieurs flux doivent pouvoir tourner ensemble. L'analyse du moteur a montré : aucun recouvrement entre un nœud
et son amont ; des connexions DuckDB sans limite en nombre ni en taille ; des modificateurs spatiaux qui chargeaient
tout en mémoire sur un seul cœur ; des vues d'inspection construites pour chaque port pendant le run.

## Décision
1. **Pipeline.** Un nœud à une seule liaison entrante est branché en direct sur son amont (`cache.Tap` /
   `PortStream`, file de 4 lots) : il démarre avec lui. Exclus : nœuds à plusieurs entrées, voie SQL, paramètres
   tardifs (variables posées en cours de run), amont ou nœud réutilisé du cache. Les ports restent écrits (cache,
   empreintes, inspecteur), sauf en run sans cache quand tous leurs consommateurs sont en direct. Un consommateur qui
   s'arrête ferme sa file : l'amont ne reste jamais bloqué ; une erreur ou une annulation de l'amont arrête les
   consommateurs. `ETL_PIPELINE=0` revient à l'ancien comportement.
2. **Budget par run.** `duck.configure_run` fixe la mémoire et les cœurs du run (sa part de la machine) ;
   `duck.connect(kind)` en tire la limite de chaque connexion (expressions : un thread ; nœuds lourds : 60 % du
   budget divisé par `ETL_BLOCKING_NODES`; inspecteur : un quart de la machine). Le débordement DuckDB va dans le
   dossier du run.
   *Complément du 2026-10-29* : les 60 % ne sont divisés que par le nombre de nœuds lourds du run, au plus
   `ETL_BLOCKING_NODES` (un seul nœud lourd les reçoit entiers, `duck.heavy_share`). La taille des lots lus par la
   voie par lots découle des 40 % restants, partagés entre les lots en cours (`duck.batch_bytes`), au lieu de 64 Mo
   fixes. Le budget de *Paramètres → Calcul* reste la seule référence. Le dossier du cache (où va le débordement) se
   choisit dans *Paramètres → Calcul* ; un disque plein donne un message qui le dit (`duck.resource_error`).
3. **Nœuds lourds limités** : au plus `ETL_BLOCKING_NODES` (2) nœuds bloquants s'exécutent en même temps.
4. **Garde-fou.** L'échantillonneur de mémoire du run annule celui-ci quand il dépasse sa part **et** que la mémoire
   disponible du poste passe sous 5 % (ou 512 Mo), avec un message qui nomme les nœuds en cours. `MemoryError` et les
   erreurs de mémoire de DuckDB donnent le même message, sans rejeter les lignes.
5. **Modificateurs spatiaux.** Le côté « référence » est collecté et indexé une fois (`spatial.SpatialIndex`), le
   côté « candidat » est lu lot par lot et traité en parallèle (`sdk.ordered_map`, ordre conservé). La superposition
   de polygones découpe par paquets de polygones voisins (exact : un morceau n'est produit que par le paquet qui possède
   son plus petit polygone). La fusion de polygones fait une union en arbre sur des morceaux voisins (ordre de Morton).
6. **Runs parallèles.** Poste de bureau : `ETL_MAX_RUNNING` = nombre de workers, pas de plafond par utilisateur ;
   chaque run reçoit son budget divisé par le nombre de runs en cours ; les lots de la CLI (`--parallel`) aussi.
7. **Inspecteur** (*remplacé par l'[ADR 0030](0030-inspecteur-n-premieres-entites-et-tuiles.md) : N commun au
   tableau et à la carte, tuiles vectorielles construites à la demande*). Plus de vue d'inspection ni de tuiles vectorielles. La table lit le port ; la carte reçoit les N
   premières entités (2 000 par défaut, au choix 10 000 et 50 000) de la table filtrée et triée, en GeoJSON WGS84
   (`POST …/features`). L'entité sélectionnée est surlignée à part, même hors des N premières. *Décision de
   l'utilisateur : la carte n'a pas besoin de toutes les entités ; la pagination de la table suffit.*
8. **Écritures tout ou rien.** Les écrivains Parquet et CSV écrivent dans un fichier caché et le renomment à la
   fermeture ; `abort` le supprime (une annulation ne laisse plus un fichier tronqué).

## Conséquences
- Un modificateur écrit par l'utilisateur gagne le pipeline sans rien changer, tant qu'il lit son entrée une seule fois.
- Le coût de l'inspecteur n'est plus payé par le run ; une carte au-delà de 100 000 entités n'est pas proposée.
- Le premier run d'un nœud lourd peut toujours manquer de mémoire si une seule entrée dépasse la RAM : le message dit
  où et comment réduire.
