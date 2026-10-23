# Questions ouvertes

Les questions sont regroupées ici pour ne pas interrompre l'avancement. Chacune porte
l'hypothèse retenue en attendant la réponse.

## Q2. Droits et authentification — tranchée : pas de droits fins

Décision : pas de droits par flux ou par connexion pour l'instant. L'atelier n'authentifie pas ; un proxy
d'entreprise authentifie et renseigne `X-ETL-User` (voir [securite.md](securite.md)).

## Q3. Versions des composants — réglée

Décision : versions explicites, épinglables dans le nœud. Voir [ADR 0017](decisions/0017-versions-de-composants.md).

## Q4. Tuiles à 10 M d'entités — R-tree abandonné

Médiane 435 à 587 ms à z7-z9 sur 10 M de points (seuil de l'ADR 0010 : 500 ms). Un essai d'index R-tree dans une
base DuckDB (10 M de points, poste de dev) a donné 5 à 10 s par tuile à z7-z9 contre 0,6 s pour le Parquet trié :
les grosses tuiles renvoient des centaines de milliers de lignes, que l'index ne fait que retrouver une à une.
**Décision** : R-tree abandonné, tuiles inchangées. Piste non retenue : des copies échantillonnées de la vue pour
les petits zooms.
