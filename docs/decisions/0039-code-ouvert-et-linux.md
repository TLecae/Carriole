# ADR 0039 — Code ouvert, logo « i/o », version Linux

- **Statut** : accepté
- **Date** : 2026-10-20
- **Modifie** : ADR 0031 points 2 (logo) et 4 (identifiant `appId`)

## Contexte
Carriole est publié en open source sur GitHub (https://github.com/TLecae/Carriole), avec une page de présentation et
des installeurs téléchargeables gratuitement. Le logo (une charrette chargée) se lisait mal en petit, dans la barre
des tâches. Des utilisateurs travaillent sous Linux.

## Décision
1. **Licence MIT** (`LICENSE`), auteur Thomas Lecae. Les composants tiers livrés dans les installeurs sont listés
   dans `THIRD_PARTY_NOTICES.md` (`scripts/third_party_notices.py`), livré avec l'application.
2. **Dépôt public sans l'historique de développement** : il contenait des flux FME et des références internes. Le
   dépôt public part d'un commit initial.
3. **Logo** : seulement « i/o » en ambre sur le carré bleu (mêmes couleurs), lisible en 16 px. Le nom s'écrit
   « Carr**i/o**le » (le « i/o » en ambre) dans la barre du haut et la fenêtre d'ouverture.
4. **Identifiant** `appId` : `io.github.tlecae.carriole`. Une installation antérieure (ancien identifiant) n'est pas
   remplacée par l'installeur : la désinstaller d'abord.
5. **Linux : AppImage** (`Carriole-<version>-x86_64.AppImage`), un seul fichier, sans droits d'administrateur,
   construit par `scripts/build_desktop.sh` (Python géré par uv, relocalisable). Dossiers XDG
   (`~/.local/share/carriole`, `~/.config/carriole`) côté moteur comme côté application. Sans coffre DPAPI, la clé
   de chiffrement des secrets est un fichier aux droits 0600.
6. **Distribution** : GitHub Releases (gratuit, sans quota de téléchargement pour un dépôt public) ; les deux
   installeurs sont construits par `.github/workflows/release.yml` au push d'une étiquette `v*`. Page de
   présentation : GitHub Pages (`site/`, `.github/workflows/pages.yml`).

## Conséquences
- Les installeurs ne sont pas signés : avertissement SmartScreen sous Windows (documenté). Piste gratuite :
  SignPath Foundation pour les projets open source.
- L'AppImage contient le moteur, qui ne peut pas lui survivre (le dossier monté disparaît à la fermeture) : à la
  fermeture avec des runs en cours, « Laisser tourner en arrière-plan » garde l'application, sans fenêtre, jusqu'à la
  fin des runs, puis arrête le moteur. Démarrer avec la session n'est pas proposé par Electron sous Linux.
