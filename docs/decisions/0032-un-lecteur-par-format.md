# ADR 0032 — Un lecteur et un écrivain par format de fichier

- **Statut** : accepté
- **Date** : 2026-10-13

## Contexte
« Lecture de fichier » et « Écriture de fichier » servaient tous les formats : leur formulaire mêlait le séparateur
et les colonnes X/Y du CSV, la couche du GeoPackage, la feuille et la plage d'Excel, la compression de GeoParquet…
chacun annoté « (CSV) », « (Excel) ». L'utilisateur demande un modificateur par format, pour
séparer les réglages et faciliter la prise en main.

## Décision
1. **Un lecteur et un écrivain par format** (`modifiers/file_io.py`) : CSV, Excel, GeoPackage, Shapefile,
   GeoJSON, FlatGeobuf, GeoParquet, MapInfo (TAB ou MIF selon l'extension). Chacun n'a que les réglages de son
   format, le chemin en premier ; « Parcourir… » ne montre que les fichiers de ses extensions
   (`x-etl-extensions` dans le schéma du champ).
2. **Catégories** « Lecture » et « Écriture » dans la palette, avec PostgreSQL / PostGIS. « Entrées / sorties » garde
   les sources qui ne sont pas des fichiers (création d'entités, API web, SQL).
3. **Mécanique commune** : `FileReader` / `FileWriter` (motifs de fichiers, schéma, lots, écriture locale puis
   publiée sur un partage) ; ces classes de base n'ont pas de nom, elles ne sont pas dans la palette.
4. **Flux existants** : à l'ouverture (validation du flux), un « Reader » / « Writer » dont le format se déduit (format
   choisi, sinon extension du chemin) devient le modificateur de son format, avec ses seuls réglages (la couche
   d'Excel devient la feuille). Si le format ne se déduit pas (chemin en variable `${SRC}` sans extension…),
   l'ancien nœud reste : `Reader` et `Writer` sont toujours exécutés, mais cachés (`hidden`) de la palette, de
   l'ajout rapide, du catalogue et de l'assistant.
5. « Écrire un fichier par groupe » (SplitToFiles) reste générique : son chemin change de format selon les valeurs.

## Conséquences
- Un nœud converti change d'empreinte : le premier run après conversion ne réutilise pas le cache de ce nœud.
- Un format de plus = deux petites classes dans `file_io.py` (et leurs traductions).
