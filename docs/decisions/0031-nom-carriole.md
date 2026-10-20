# ADR 0031 — L'application s'appelle Carriole

- **Statut** : accepté ; logo et identifiant `appId` remplacés par l'[ADR 0039](0039-code-ouvert-et-linux.md)
- **Date** : 2026-10-13

## Contexte
« Atelier ETL » décrivait l'outil sans le nommer. L'utilisateur choisit **Carriole** : la charrette qu'on charge
(« carry all » en anglais), et le « io » du nom, entrées et sorties (i/o), ce que fait un flux.

## Décision
1. **Nom visible : Carriole** partout où l'utilisateur le lit : fenêtre, barre du haut, icône de la zone de
   notification, installeur (`Carriole-Setup-<version>.exe`, `C:\Program Files\Carriole`), raccourcis,
   notifications et courriels, assistant, documentation. Dans la barre du haut, le « io » du nom est en ambre.
2. **Logo** (`desktop/build/make_icon.py`, qui écrit l'icône Windows et `frontend/public/logo.svg`) : une charrette
   chargée de blocs de données ; son montant est un « i », sa béquille une « / », sa roue un « o » : on lit
   « i/o » sous la charge. Lisible jusqu'à 32 px.
3. **Dossiers** : `%LOCALAPPDATA%\Carriole` et `%APPDATA%\Carriole`. Au premier lancement, les dossiers
   `AtelierETL` sont renommés (base des runs, flux récents, cache, réglages, clé de chiffrement : rien n'est perdu),
   par l'application comme par le moteur (`settings._folder`, `backend.ts`). S'ils ne peuvent pas l'être (une
   ancienne version encore lancée), ils restent utilisés.
4. **Mise à jour sans double installation** : l'identifiant de l'application (`appId`)
   ne change pas, l'installeur reconnaît donc l'ancienne « Atelier ETL » et la
   remplace. La langue choisie à l'installation est écrite dans `HKLM\Software\Carriole` (l'ancienne clé est
   encore lue, et effacée à la désinstallation).
5. **Inchangé** (invisible pour l'utilisateur, coûteux à renommer) : les paquets Python `etl_engine`, `etl_api`…,
   la commande `etl`, les variables `ETL_*`, l'identifiant `appId`.

## Conséquences
- Les anciens chemins cités dans le journal et les ADR précédentes restent tels quels (historique).
- `desktop/build` (script de l'installeur, logo, préparation du Python embarqué) était ignoré par git par la règle
  générale `build/` : il est désormais suivi, sauf le Python embarqué.
