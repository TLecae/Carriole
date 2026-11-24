# ADR 0060 — Renommer un flux depuis l'éditeur

- **Statut** : accepté
- **Date** : 2026-11-24
- **Complète** : ADR 0021 (flux et composants en fichiers)

## Contexte
En base, changer le nom d'un flux puis l'enregistrer renommait le flux sans rien demander : l'utilisateur qui voulait
partir d'un flux existant pour en faire un autre écrasait l'original. En fichiers, le nom (celui du fichier) n'était
pas modifiable dans l'éditeur : seul « Enregistrer sous… » en donnait un autre.

## Décision
1. Le nom du flux se modifie dans la barre du haut, en base comme en fichiers.
2. Enregistrer un flux déjà enregistré dont le nom a changé demande quoi faire :
   - **créer un nouveau flux** (choix par défaut) : en base, un nouveau flux ; en fichiers, un nouveau fichier
     `<nom>.json` dans le dossier de l'original. L'original reste tel quel ;
   - **renommer** : en base, le flux prend le nouveau nom ; en fichiers, le fichier est renommé dans son dossier
     (`POST /flows/{id}/rename`) ;
   - annuler : rien n'est enregistré.
3. Le renommage d'un fichier écrit d'abord le nouveau fichier, puis retire l'ancien : jamais d'écrasement d'un autre
   fichier (409), rien de perdu si l'ancien ne peut pas être retiré.

## Conséquences
- En fichiers, l'identifiant d'un flux est son chemin : les runs passés d'un fichier renommé ne sont plus rattachés au
  flux dans la page d'accueil (ils restent dans l'historique des runs).
