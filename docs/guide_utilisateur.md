# Guide de l'utilisateur

Pour qui : les personnes qui construisent des flux de traitement de données (SIG, tableurs, bases). Pas besoin de
savoir programmer. Pour **écrire un nouveau modificateur en Python**, voir [ecrire_un_modificateur.md](ecrire_un_modificateur.md).

## 1. Le principe

Un **flux** est un schéma : des **nœuds** (chacun fait une opération) reliés par des **connexions**. Les données
(des *entités* : une ligne de tableau, éventuellement avec une géométrie) circulent de gauche à droite. Le flux
s'exécute en arrière-plan sur votre poste ; pour fermer la fenêtre sans interrompre un run, activer *Laisser Carriole
tourner en arrière-plan* (*Paramètres → Général*), sinon la fermeture propose de laisser finir les runs en cours.

Chaque nœud a des **ports** : des entrées à gauche, des sorties à droite. Un nœud qui sait qu'il peut échouer sur
certaines entités a une sortie **Rejected** : les entités en erreur y partent avec le motif
(`_rejection_reason`) au lieu d'arrêter le flux. Une erreur de **configuration** (attribut introuvable, fichier
absent) arrête en revanche le flux, avec un message qui dit quel nœud et pourquoi.

## 2. Construire un flux

Un **flux est un fichier** `.json`, rangé où vous voulez (le plus souvent sur le partage de l'équipe).
À l'ouverture, la **page d'accueil** liste vos flux **récents** (description, emplacement, nombre de nœuds, état du
dernier run) : un clic ouvre un flux, **Ouvrir un flux…** en choisit un autre fichier, **+ Nouveau flux** en commence un,
la recherche filtre la liste. La croix **retire** un flux de la liste sans jamais supprimer son fichier. Dans l'éditeur,
**⌂ Accueil** y ramène sans rien perdre : **Reprendre** retrouve l'éditeur tel qu'on l'a laissé.

1. **Ajouter un nœud** : double-clic dans la palette (à gauche : catégories repliées, un clic en déplie une ; la
   recherche en haut les déplie toutes), ou, sur le canvas, **taper quelques lettres** : un champ d'ajout rapide s'ouvre ; Entrée ajoute le premier résultat et le **relie au nœud
   sélectionné**.
2. **Relier** deux nœuds : tirer d'une sortie vers une entrée. Les attributs disponibles en amont sont proposés dans
   les formulaires ; un avertissement apparaît (onglet *Avertissements*) quand un attribut n'existe pas en amont ou
   qu'une entrée n'est pas connectée.
3. **Régler** un nœud : cliquer dessus, le formulaire est à droite. Sous chaque champ, une phrase dit **à quoi il
   sert et ce que signifie un champ vide** ; le champ vide affiche un **exemple** de saisie (grisé), et le
   survol d'un libellé rappelle l'aide. L'*Aide* en bas du formulaire détaille le modificateur. Les champs
   *Expression* acceptent du SQL (DuckDB) : `"pop" >= 1000`, `upper("nom")`, `ST_Area(geometry)`. Les noms
   d'attributs s'écrivent entre guillemets doubles.
4. **Enregistrer** : `Ctrl+S`. Un flux neuf s'appelle « Nouveau flux » et porte la mention *non enregistré* ; le
   premier enregistrement demande où ranger le fichier, et **le flux prend le nom de ce fichier** (un seul nom : pas
   de nom à saisir à part). Les enregistrements suivants réécrivent le même fichier (un point signale des
   modifications non enregistrées). **Enregistrer sous…** (`Ctrl+Maj+S`) crée une copie ailleurs, ou sous un autre
   nom : c'est ainsi qu'on renomme un flux. Si un collègue a modifié le fichier depuis
   que vous l'avez ouvert, l'enregistrement est **refusé** (rien n'est écrasé) : *Enregistrer sous…* garde votre travail.

Sélection : cliquer un nœud, ou tirer un cadre sur le fond du canvas ; `Ctrl`+clic (ou `Maj`+clic) ajoute un nœud à
la sélection ou l'en retire. Tirer l'un des nœuds sélectionnés déplace tout le groupe. Un clic sur un nœud montre
ses données dans l'onglet *Données* (ou dit qu'il n'en a pas encore).

**Bandeau du nœud** : au-dessus du nœud sélectionné, trois icônes (une bulle d'aide au survol) : **▶|** lancer le
flux jusqu'à ce nœud, **⏻** le désactiver ou le réactiver, **▦** voir ses données.

Raccourcis : `Ctrl+Z` / `Ctrl+Y` annuler / rétablir, `Ctrl+C` / `Ctrl+V` copier / coller des nœuds, `Alt+D`
activer / désactiver les nœuds sélectionnés (un nœud désactivé est ignoré avec son aval). Le menu **Organiser** :
annotations, signets (groupes repliables), alignements.

### Paramètres publiés
Dans le panneau *Flux* (clic sur le fond du canvas) : des valeurs saisies **au lancement** (un département, un
millésime, un fichier). Dans n'importe quel paramètre de nœud : `${NOM}` ; variable d'environnement du serveur :
`${env:NOM}`.

## 3. Lancer et suivre

- **Lancer** exécute le flux ; **Jusqu'au nœud** (menu *Exécution* ou bandeau du nœud) s'arrête au nœud
  sélectionné, en reprenant le cache de tout ce qui n'a pas changé, **y compris les lectures de base de données** ;
  les nœuds des autres branches restent consultables.
- **Mise en cache** (bouton *Cache* à côté de *Lancer*, ou menu *Exécution*) : un réglage pour tout
  le flux, retenu d'une session à l'autre :
  - **activée** (par défaut) : les données de chaque nœud sont gardées, pour l'inspecteur ; au run suivant, **seuls
    les nœuds modifiés (et leur aval) tournent**, les autres sont repris du run précédent (état *cache*). Les
    écritures, les lectures de base de données, d'API web ou de STAC, le SQL et les flux lancés sont **toujours
    relancés** par *Lancer* (leur source peut changer sans que le flux change), avec tout leur aval ; *Jusqu'au
    nœud* reprend les lectures (pas les écritures). *Tout relancer* (menu *Exécution*) recalcule tout ;
  - **désactivée** : tout est recalculé et rien n'est gardé : plus rapide et moins de disque, mais pas d'inspecteur.
- **Place sur le disque** (application de bureau) : seules les données du **dernier run de chaque flux ouvert** sont
  gardées, ainsi que celles des runs en cours. Fermer le flux (ouvrir un autre flux, *Nouveau flux*) ou la fenêtre
  les supprime dans les secondes qui suivent ; après un plantage, au redémarrage. Le journal et les comptes des runs
  restent consultables dans *Runs*, sans leurs données.
- Les **comptes** s'affichent **à côté de chaque port du nœud** (entrées et sorties : `output`, `Passed`,
  `Failed`, `Rejected`…, même pour un port non relié), en direct pendant le run, abrégés (`138 k`, `1,2 M`) : le
  nombre exact est dans l'info-bulle. L'état est sur
  chaque nœud, le **journal** en bas (un clic sur une ligne sélectionne le nœud). Annuler : bouton *Arrêter*.
- **Runs** : liste de tous les runs (en cours, terminés, en échec, interrompus). On peut les rouvrir, voir le
  journal et les données, **relancer** (même flux, tel qu'il avait été exécuté) ou **reprendre** après un arrêt : les
  nœuds terminés ne sont pas recalculés. Un e-mail de fin de run est possible si le serveur le propose.

## 4. Inspecter les données

Onglet **Données** : cliquer sur un nœud ou une connexion (l'onglet s'affiche). Table (tri, filtre par colonne, recherche, clause
`WHERE` libre) et **carte** synchronisées : sélectionner une ligne la surligne sur la carte et inversement. Tout est
calculé côté serveur, même avec des millions d'entités. *Exporter* : CSV, GeoPackage ou GeoParquet de la vue
filtrée.

La table montre **toutes les entités** du port : les lignes arrivent au fil du défilement, et filtres, tri,
recherche, statistiques et export portent sur tout le port. Le nombre d'entités est affiché une fois (« 11 / 20
entités » quand un filtre en retient 11).

**Colonnes** : glisser le bord droit d'un en-tête pour élargir la colonne, double-clic pour l'ajuster au contenu.
**Copier** : clic droit sur une cellule (*Copier la valeur*, *Copier la ligne*, *Copier le nom de la colonne*) ou
sur un en-tête ; ou cliquer une cellule puis `Ctrl+C`. On peut aussi sélectionner du texte à la souris.

La première ouverture d'un port prépare sa carte **en arrière-plan** : la table est consultable tout de suite, la
carte affiche « Chargement de la carte… » (environ 30 secondes pour 1,6 million de parcelles, une seule fois).
Si les géométries sont trop volumineuses pour la mémoire, la carte le dit à sa place (la table reste consultable).
La carte est faite de tuiles vectorielles, généralisées selon le zoom : les contours sont simplifiés au
demi-pixel, une entité plus petite qu'un pixel est un point d'un pixel ; le détail revient en zoomant. La carte
dessine **au plus 50 000 entités par zone** (4 × 4 tuiles) : au-delà, **les plus grandes** (les mêmes dans toutes
les tuiles de la zone), les plus petites apparaissent en zoomant ; pour des points, un échantillon. Une tuile a toujours le même contenu, quelle que soit la vue : déplacer ou
zoomer ne recharge rien de ce qui est déjà venu, et les tuiles des petites échelles sont calculées d'avance
(environ 35 s pour 1,6 million de parcelles, après la préparation de la carte), pour le dernier port ouvert : passer
à un autre port ne le fait pas attendre les tuiles du précédent. Changer le filtre ou la couleur
garde l'ancienne image jusqu'à l'arrivée de la nouvelle. La table, elle, montre toujours toutes les entités. Sélectionner une entité énorme la surligne avec un contour
simplifié.

**Taille du panneau** : glisser la barre au-dessus des onglets (ou `↑` / `↓` quand elle a le focus) ; la hauteur est
retenue. **⤢ Pleine page** ouvre le panneau sur toute la fenêtre (Échap pour revenir à l'éditeur).

**Fond de plan** de la carte : boutons **Plan** (OpenStreetMap) et **Satellite** (Esri) ; le ⚙ ajoute la photographie
aérienne de l'IGN, aucun fond (hors ligne) ou une adresse de tuiles personnalisée. Les tuiles viennent d'internet,
chargées depuis internet ; voir l'[installation](installation.md#fonds-de-plan-de-la-carte) si la carte reste grise.

## 5. Fichiers et bases

| Besoin | Modificateur |
| --- | --- |
| Lire / écrire un fichier | un lecteur et un écrivain **par format** (catégories *Lecture* et *Écriture*) : *Lecture CSV*, *Lecture GeoPackage*, *Lecture Shapefile*, *Lecture Excel*, *Lecture GeoJSON*, *Lecture GeoParquet*, *Lecture FlatGeobuf*, *Lecture MapInfo*, et les *Écriture …* correspondants |
| Lire / écrire une base | *Lecture* / *Écriture PostgreSQL / PostGIS*, *Oracle* ; *Lecture* / *Écriture SQLite / SpatiaLite* (fichier) |
| Exécuter un ordre SQL | *SQL (créateur)* ; *SQL (par entité)* avec `{{attribut}}` |

- Les fichiers sont **ceux de votre poste et des partages auxquels vous avez accès** (`Z:`, chemins réseau). Le
  bouton **Parcourir…** à côté d'un champ fichier ouvre le sélecteur de fichiers de Windows. Le chemin saisi peut aussi
  être tapé à la main. Un motif (`D:/communes/*.gpkg`) lit plusieurs fichiers.
- Chaque lecteur ou écrivain n'a **que les réglages de son format** (séparateur et colonnes X/Y pour le CSV, couche
  pour le GeoPackage, feuille pour Excel…), et **Parcourir…** n'y montre que les fichiers de ce format. Les flux
  enregistrés avec l'ancienne *Lecture de fichier* sont convertis à l'ouverture.
- **Lignes lues au plus** (tous les lecteurs, fichiers et bases) : pour essayer un flux sur un extrait, seules les N
  premières entités sont lues (0 = toutes). Le nœud porte alors une pastille « ≤ N » ; au survol, une bulle le rappelle.
- **Écrire sur le partage** : un écrivain peut viser le partage de l'équipe. Deux flux qui modifient en même
  temps le même GeoPackage s'écrasent : éviter. Pour une tâche planifiée, préférer un chemin réseau (`//serveur/partage/…`)
  à une lettre de lecteur, que la session de la tâche ne voit pas toujours.
- **Connexions** (menu *Outils*) : on choisit le type de base (PostgreSQL / PostGIS, Oracle) puis on
  saisit une fois hôte, base (Oracle : service), utilisateur, mot de passe ; les flux ne contiennent que le *nom*. Le
  mot de passe est chiffré et jamais réaffiché. Les lecteurs et écrivains de chaque base ne proposent que les connexions de leur type ; *SQL (créateur)*
  et *SQL (par entité)* acceptent toutes les bases (le SQL est celui de la base). Rien à installer sur le poste.
- Détails par format (Shapefile 10 caractères, Excel, partitions…) : [formats.md](formats.md).

## 6. Le catalogue

Tous les modificateurs, avec leurs paramètres : [catalogue.md](catalogue.md). Quelques repères :
**Filtres** (ExtractByExpression, filtre spatial, doublons, type de géométrie), **Attributs** (créer, gérer, renommer, garder,
retirer, table de correspondance, valeurs vides, découper, éclater), **Calculs** (statistiques, agréger, compteur,
trier), **Jointures** (attributaire, fusion, relateur spatial, point dans polygone, SQL), **Géométrie** (reprojeter,
tampon, découper, fusionner, valider, **réparer**, simplifier, coordonnées, surface…).

**Géométries invalides** (auto-intersection, anneau non fermé…) : *Valider les géométries* trie les valides et les
invalides avec le motif ; *Réparer les géométries* les rend valides (les autres passent intactes) et envoie sur
*Rejected* celles qui ne donnent plus rien. À placer avant un découpage, une jointure spatiale ou une écriture
PostGIS qui refuse les géométries invalides.

## 7. Composants : réutiliser un morceau de flux

1. Construire un flux avec des nœuds **Entrée du composant** et **Sortie du composant** (un par port, nommés).
2. Panneau *Flux* → cocher **Ce flux est un composant réutilisable** ; choisir identifiant, libellé et catégorie.
   Les *paramètres publiés* du flux deviennent les paramètres du composant.
3. Enregistrer le fichier **dans un dossier de composants** (réglage `component_dirs`, en général un dossier du
   partage : toute l'équipe partage les mêmes composants). Dans les autres flux, le composant apparaît **dans la
   palette** comme un modificateur ; le bouton ↗ du nœud ouvre sa définition.

Un composant se lance seul pour être testé (indiquer un fichier d'exemple sur l'entrée). Un run garde la version du
composant utilisée : le relancer plus tard rejoue la même chose. [ADR 0014](decisions/0014-composants.md).

### Versions d'un composant
Tant que rien n'est publié, les flux utilisent le composant tel qu'il est enregistré : toute modification les
touche. Pour faire évoluer un composant sans surprise :

1. Enregistrer le composant, puis, dans le panneau du flux, **Publier une version** (avec une note : ce qui change).
   Une version est un instantané qui ne bouge plus.
2. Dans un flux qui l'utilise, sélectionner le nœud composant : **Version du composant**. Un nœud ajouté depuis la
   palette est épinglé à la dernière version ; le choix « Dernière publiée » le fait suivre les nouvelles versions.
   Un message signale quand une version plus récente existe.

Le brouillon (flux enregistré mais non publié) n'affecte pas les flux dès qu'une version existe.
[ADR 0017](decisions/0017-versions-de-composants.md).

## 8. Planifier

Bouton **Planification** : choisir un flux *enregistré*, une expression cron (des modèles sont proposés ; les
prochaines échéances sont affichées), les paramètres publiés, un e-mail éventuel. Les workers lancent le flux à
l'heure, sans navigateur ouvert. *Lancer maintenant* teste la planification. Une échéance ignorée (run précédent
encore en cours, workers arrêtés) est indiquée dans la ligne de la planification. [ADR 0015](decisions/0015-planification.md).

Format cron : `minute heure jour-du-mois mois jour-de-semaine`. Exemples : `0 2 * * *` chaque nuit à 2 h,
`30 6 * * 1-5` jours ouvrés à 6 h 30, `0 3 1 * *` le 1er du mois à 3 h.

## 9. En cas de problème

| Symptôme | Piste |
| --- | --- |
| « connexion inconnue » | la créer dans *Connexions* (même nom) |
| « mot de passe … illisible » | la clé de chiffrement de ce poste a changé (autre compte, autre poste) : ressaisir le mot de passe |
| Run « interrompu » | un worker s'est arrêté : *Reprendre* depuis la liste des runs |
| Bandeau « Le moteur ne répond plus » | l'application le relance seule (quelques secondes) ; ne pas fermer la fenêtre : un enregistrement demandé entre-temps est fait au retour. Détail dans `logs\server.log` (*Paramètres* → dossier des journaux) |
| Nœud rouge avec « configuration : … » | le message dit quoi corriger ; le journal donne le détail |
| Données purgées | les données d'un flux fermé sont supprimées (sur un serveur : après quelques jours) : relancer le flux |
| « Le flux a été modifié par quelqu'un d'autre » | le fichier a changé sur le disque : *Enregistrer sous…* pour garder votre version |

## 10. Variables, dossiers et lots

**Variables** : dans n'importe quel paramètre de nœud, `${…}` est remplacé par une valeur. Le sélecteur `${…}` à côté
des champs liste ce qui est disponible, et la valeur résolue s'affiche sous le champ.

| Écriture | Valeur |
| --- | --- |
| `${NOM}` | un paramètre publié, ou une **variable du flux** (panneau *Flux* → *Variables du flux* : constantes, ou valeurs qui en citent d'autres : `${RACINE}/sortie`) |
| `${sys:date}`, `${sys:flow_dir}`, `${sys:run_id}`… | le contexte : date, heure, utilisateur, dossier et nom du flux, numéro du run, dossier temporaire |
| `${global:NOM}` | une variable commune à tous vos flux (*Paramètres → Variables globales*) |
| `${env:NOM}` | une variable d'environnement |
| `${var:NOM}` | une valeur **tirée des données** par un nœud *Poser des variables* placé en amont |

**Dossiers de fichiers** : *Lister des fichiers* donne une entité par fichier (chemin, nom, taille, date) ; *Lire les
fichiers listés* les lit tous et réunit leurs colonnes ; *Écrire un fichier par groupe* range les résultats dans un fichier
par valeur d'un attribut (`D:\sorties\{departement}\communes.gpkg`). *Lancer un flux* exécute un autre flux pour chaque
fichier.

**Lots** : le bouton **En lot…** lance le flux une fois par fichier d'un motif (`D:/communes/*.shp`) ou par valeur d'une
liste : on choisit le paramètre publié qui reçoit chaque valeur. Les runs sont regroupés dans la liste des runs. En ligne de commande :
`etl run flux.json --each "D:\communes\*.shp" --as SRC --parallel 4` (voir [installation](installation.md#ligne-de-commande)).

## 11. Rasters (MNT, images satellite)

Un raster est une image géoréférencée (GeoTIFF, COG, ASC, JP2, VRT…). Dans un flux, **un port raster contient un
raster par ligne** : la ligne décrit le fichier (taille, bandes, système de coordonnées) et porte son emprise ; les
pixels restent dans les fichiers et ne sont lus que par fenêtres, donc la mémoire ne dépend pas de la taille de l'image.

- **Lire** : `RasterReader` (fichier, motif `*.tif`, liste, adresse `https://`). **Chercher** : `STACSearch` (Sentinel-2,
  Landsat… sur un catalogue STAC : emprise, dates, couverture nuageuse, assets voulus).
- **Calculer** : `RasterCalculator` (NDVI : `(nir - red) / (nir + red)`, les noms de bandes sont les variables),
  `ReclassifyRaster`, `TerrainAnalysis` (pente, exposition, ombrage), `Contour`.
- **Géométrie** : `WarpRaster`, `ResampleRaster`, `ClipRasterByMask`, `MergeRasters` (dalles ou composite dans le
  temps), `StackBands`.
- **Vers les entités** : `ZonalStatistics` (moyenne, somme… par commune), `SampleRasterValues`, `Polygonize`,
  `RasterStatistics` ; **des entités vers un raster** : `Rasterize`. **Écrire** : `RasterWriter`.
- **Inspecter** : sélectionner un nœud raster dans l'onglet Données. L'emprise des rasters s'affiche sur la carte avec
  l'image (palette ou composition rouge-vert-bleu, étendue réglable) ; cliquer sur la carte donne la valeur de chaque
  bande au pixel visé. Sélectionner une autre ligne de la table change de raster.
- Les images distantes (STAC, COG) passent par le proxy du poste et ne sont lues que par plages.

## 12. L'assistant IA

Le bouton **✨ Assistant** ouvre un panneau où l'on décrit ce que l'on veut en français ou en anglais : créer un flux, expliquer
une erreur, comprendre un nœud, écrire un modificateur. L'assistant **lit** (catalogue, flux ouvert, fichiers, runs) mais ne
**change rien tout seul** : ses modifications arrivent sous forme de propositions (liste des changements, avertissements de
l'analyse) que l'on **applique d'un clic**, ou que l'on ignore. Une proposition appliquée s'annule avec Ctrl+Z.

- **Configurer** (⚙) : ajouter Claude (Anthropic), OpenAI, OpenRouter ou un modèle local (Ollama) ; saisir la clé d'API
  (chiffrée sur le poste, jamais affichée), lire la liste des modèles chez le fournisseur et en choisir un.
- **Références `@`** : taper `@` pour joindre un nœud (`@Reader_1`), la sortie d'un run (`@Reader_1.output`) ou un fichier
  (`Choisir un fichier…`, ou le glisser dans le panneau avec l'application de bureau). L'assistant en reçoit un résumé.
- **Confidentialité** : le bandeau 🔒 du panneau dit ce qui est transmis. Par défaut un fournisseur distant ne reçoit que des
  **schémas** (noms et types de champs, nombres, système de coordonnées), jamais de valeurs ; un modèle local peut recevoir
  quelques lignes d'exemple (réglable). Le texte écrit, le flux ouvert (paramètres et chemins compris) et les messages
  du journal sont toujours transmis : ne mettez pas de mot de passe dans un flux (utilisez les connexions ou `${env:NOM}`).
- **Écrire un modificateur** : l'assistant propose le code et son test ; après relecture, « Tester et installer » lance le test
  dans un processus isolé et n'installe le plugin que s'il réussit. Le plugin tourne ensuite avec vos droits.
- En ligne de commande : `etl ai "ajoute un filtre sur la population" --flow flux.json --out flux2.json`.
