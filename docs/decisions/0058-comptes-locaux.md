# ADR 0058 — Comptes locaux gérés par l'administrateur

- **Statut** : accepté
- **Date** : 2026-11-11
- **Complète** : ADR 0052 (mode serveur et authentification)

## Contexte
Les comptes du fournisseur `file` ne se créaient qu'en ligne de commande (`etl users add`), dans le conteneur : peu
commode pour un administrateur qui n'est pas développeur, et contraire à la règle « tout se règle dans Carriole ». Une
organisation qui a un annuaire veut aussi ouvrir l'accès à quelques personnes qui n'y sont pas (prestataires,
partenaires), et garder la main si l'annuaire est arrêté. Enfin, un serveur neuf sans compte ne permettait à personne
de se connecter.

## Décision
1. **Gestion dans l'interface** (*Outils → Comptes*, administrateurs) : créer un compte (identifiant, nom affiché,
   groupes, mot de passe), modifier son nom ou ses groupes, lui donner un nouveau mot de passe, le supprimer.
   Changer les groupes ou le mot de passe ferme ses sessions ; on ne supprime pas son propre compte. Routes
   `/api/accounts` ; chaque action va au journal d'activité, sans mot de passe.
2. **Son propre mot de passe** : un utilisateur d'un compte local le change depuis son menu (l'actuel est redemandé).
3. **Fournisseurs combinés** : `ETL_AUTH=ldap,file` essaie l'annuaire, puis les comptes locaux ; un annuaire
   injoignable n'empêche pas les comptes locaux. `proxy` ne se combine pas (l'identité vient alors du proxy).
4. **Premier administrateur** : au démarrage, s'il n'existe aucun compte local et que `ETL_ADMIN_PASSWORD` (ou
   `_FILE`) est défini, le compte `ETL_ADMIN_USER` (« admin ») est créé dans le premier groupe de `ETL_ADMINS`
   (`carriole-admins` par défaut). Ensuite, la variable ne change plus rien. Avec `file` seul, un serveur sans compte
   ni cette variable refuse de démarrer et le dit.
5. Mots de passe : 8 caractères au moins, hachés par scrypt ; identifiants sans casse. `etl users` reste disponible.

## Conséquences
- Les comptes d'un annuaire ou d'un proxy ne se gèrent pas dans Carriole : ils restent dans le système de
  l'organisation.
- Plusieurs processus d'API devant la même base gardent jusqu'à 20 s une session fermée ailleurs (cache des
  sessions) ; Carriole n'en lance qu'un.
