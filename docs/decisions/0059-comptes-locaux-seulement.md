# ADR 0059 — Comptes locaux seulement

- **Statut** : accepté
- **Date** : 2026-11-11
- **Remplace en partie** : ADR 0052 (fournisseurs d'identité), 0058 (combinaison avec un annuaire)

## Contexte
Le mode serveur proposait trois fournisseurs d'identité interchangeables (annuaire LDAP, proxy d'authentification,
comptes locaux) et leur combinaison. Pour une première version du serveur, c'est trop : plus de réglages à expliquer,
une dépendance de plus (`ldap3`), plus de surface d'attaque, et des cas à tester (annuaire injoignable, compte de
service, en-têtes d'un proxy) avant même que le serveur ait servi.

## Décision
1. Une seule façon de se connecter : un compte local (identifiant et mot de passe vérifiés par Carriole), créé par
   un administrateur dans *Outils → Comptes* ; le premier par `ETL_ADMIN_PASSWORD`.
2. L'annuaire LDAP, le proxy d'authentification, leur combinaison et le réglage `ETL_AUTH` sont retirés, avec la
   dépendance `ldap3`. Le code reste dans l'historique du dépôt.
3. `ETL_TRUSTED_PROXIES` garde un rôle : le proxy HTTPS placé devant le serveur transmet l'adresse du client et le
   schéma (`X-Forwarded-For`, `X-Forwarded-Proto`) ; un en-tête d'identité n'est jamais cru.
4. Sessions, rôles, partages (utilisateurs et groupes des comptes), journal et quotas ne changent pas.

## Conséquences
- Un utilisateur a un mot de passe de plus, géré par l'administrateur de Carriole.
- Un annuaire ou une connexion unique de l'organisation reste possible plus tard : un fournisseur de plus derrière
  `LocalAccounts.authenticate`, sans toucher aux sessions ni aux droits.
