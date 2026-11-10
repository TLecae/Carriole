# ADR 0054 — Droits et partage en mode serveur

- **Statut** : accepté
- **Date** : 2026-11-10
- **Complète** : ADR 0052 (mode serveur), 0011 (connexions), 0015 (planification)

## Contexte
Sur un serveur partagé, chacun voyait les flux, les runs, les données inspectées et les connexions (donc les bases)
de tous. Une équipe veut garder ses flux pour elle, en confier certains à d'autres, et ne prêter une connexion qu'à
ceux qui doivent s'en servir.

## Décision
1. **Propriétaire** : celui qui crée un flux ou une connexion. Lui et les administrateurs ont tous les droits.
2. **Partage** (table `shares`, schéma v10) : un rôle `read` ou `write` donné à `user:nom`, `group:nom` (groupes lus à
   la connexion : annuaire, proxy, fichier) ou `*` (tout le monde). Seul le propriétaire modifie les partages.
   - flux : `read` = ouvrir, lancer, inspecter ses runs ; `write` = en plus enregistrer, arrêter ou mettre en pause
     ses runs ; supprimer reste au propriétaire ;
   - connexion : `read` = l'utiliser dans un flux, voir ses tables ; `write` = en plus changer ses réglages ;
   - un composant reste utilisable par tous (palette) ; le modifier ou publier une version suit ses partages.
3. **Visibilité** : ce qu'on ne peut pas lire n'existe pas (404) ; ce qu'on lit sans pouvoir le modifier est refusé
   avec la raison (403). Un run est visible de son propriétaire et de ceux qui lisent son flux ; l'inspecteur et les
   tuiles le vérifient. Une planification appartient à son créateur.
4. **Connexions vérifiées deux fois** : à la création du run (champs « connexion » des nœuds) et dans le worker, au
   moment de l'ouvrir, pour le propriétaire du run (groupes relus dans `users`) : une planification d'un flux qui
   n'est plus partagé, ou dont la connexion ne l'est plus, échoue avec la raison.
5. **Flux partagé en lecture** : l'éditeur l'ouvre avec l'étiquette « lecture seule » ; *Enregistrer* en fait une
   copie au nom de l'utilisateur.
6. Sur le poste de bureau, un seul utilisateur : aucune vérification.

## Conséquences
- Interface : *Partager…* sur les cartes de l'accueil, dans le menu *Fichier* et dans *Connexions* ; les destinataires
  proposés sont les utilisateurs déjà connectés et leurs groupes.
- Les droits ne s'appliquent qu'aux objets de la base : les fichiers restent régis par les dossiers autorisés
  (ADR 0052).
