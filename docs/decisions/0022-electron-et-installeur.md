# ADR 0022 — Fenêtre Electron et installeur Windows

- **Statut** : accepté (révisé le 2026-10-12 : installation pour tous les utilisateurs, avec assistant)
- **Date** : 2026-10-11
- **Complète** : [ADR 0020](0020-application-de-bureau.md)

## Contexte
Le jalon 9 donne un backend local utilisable dans un navigateur. Il faut un vrai programme : une fenêtre, des dialogues
de fichiers natifs (vrais chemins, partages réseau), une icône dans la zone de notification pour que les planifications
tournent, et une installation qu'un utilisateur sans compétence technique réalise par un double-clic : **aucune ligne de
commande**.

## Décision
- **Electron** (`desktop/`, TypeScript) charge l'interface servie par le backend local ; il ne contient aucune logique
  métier. Fenêtre en bac à sable (`contextIsolation`, `sandbox`, pas de `nodeIntegration`) ; navigation limitée à
  l'origine du backend, les liens externes (http/https seulement) s'ouvrent dans le navigateur ; les dialogues natifs
  et les actions de la fenêtre Paramètres (`etl:pick-file`, `etl:pick-folder`, démarrage avec Windows, dossiers de
  l'application, redémarrage) ne répondent qu'à la page de l'atelier. La barre de menus native est masquée (Alt) : l'interface a ses
  propres menus.
- **Le backend est détaché de la fenêtre** : `etl serve` est lancé s'il ne tourne pas (ou réutilisé s'il a été lancé par
  la ligne de commande) et survit à Electron. Fermer la fenêtre la masque (zone de notification) ; **Quitter** arrête le
  moteur, après avoir demandé quoi faire des runs en cours (les laisser finir, ou les interrompre : reprenables).
  *Démarrer avec Windows* (Paramètres) lance l'application masquée pour que les planifications tournent.
- **Instance unique** : un second lancement donne le focus et transmet le flux demandé (`Atelier ETL.exe flux.json`).
- **Python embarqué** : le Python géré par uv (relocalisable) copié dans `resources/runtime/python`, avec les dépendances
  figées par `uv.lock` et les paquets de l'atelier ; l'extension spatiale de DuckDB (`runtime/duckdb_ext`) et les
  grilles PROJ françaises (`runtime/proj`) sont téléchargées **au build**, jamais chez l'utilisateur
  (`scripts/build_desktop.ps1`, `desktop/build/prepare_runtime.py`). `etl_engine.install` retrouve ces ressources et
  pose les variables correspondantes quand ce Python est celui de l'installation. Le bytecode (`.pyc`) est compilé au
  build : le dossier du programme est en lecture seule pour l'utilisateur, rien n'y est écrit à l'exécution.
- **Installeur NSIS à assistant, pour tous les utilisateurs** (electron-builder : `oneClick: false`, `perMachine: true`) :
  l'exécutable demande les droits d'administrateur au lancement (fenêtre habituelle de Windows), propose la langue puis
  le dossier (`C:\Program Files\Atelier ETL` par défaut), crée les raccourcis pour tous les utilisateurs. L'application
  elle-même tourne sans droits particuliers (`asInvoker`). L'installeur :
  - écrit la langue choisie dans `HKLM\Software\AtelierETL` : chaque utilisateur la reprend à son premier lancement ;
  - ajoute `resources\bin` au PATH de la **machine** (`path.ps1`), d'où `etl` dans tout nouveau terminal (facultatif :
    l'application n'en a pas besoin) ;
  - arrête le moteur avant une mise à jour ou une désinstallation (`stop.ps1`).
  Les données de chaque utilisateur (`%LOCALAPPDATA%\AtelierETL`, `%APPDATA%\AtelierETL`) ne sont jamais supprimées par
  la désinstallation.
- **Pas de signature de code** : SmartScreen avertit (« Informations complémentaires » puis « Exécuter quand même »).
  `signAndEditExecutable: false` évite aussi le téléchargement de winCodeSign, qui exige le droit de créer des liens
  symboliques. À reprendre si un certificat devient disponible.
- **Mise à jour = réinstallation** de l'installeur plus récent par-dessus (pas de mise à jour automatique).

## Conséquences
- L'installeur pèse plusieurs centaines de Mo (Chromium, Python, pyarrow, DuckDB, GDAL) ; l'installation, environ 900 Mo.
- Installer exige un compte administrateur (ou son mot de passe), une seule fois par poste.
- Les textes du processus principal (menus, dialogues) suivent la langue de l'application ([ADR 0023](0023-internationalisation.md)).
- Les planifications ne tournent que tant que l'application (ou `etl serve`) tourne.
