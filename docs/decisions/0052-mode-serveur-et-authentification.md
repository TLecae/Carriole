# ADR 0052 — Mode serveur et authentification

- **Statut** : accepté
- **Date** : 2026-11-10
- **Complète** : ADR 0020 (« cible future : Linux et cloud »), 0016 (dossiers autorisés)

## Contexte
Une équipe veut partager une machine puissante et utiliser Carriole sans rien installer, dans un navigateur. Le moteur,
le store et l'API ne dépendent pas d'Electron (ADR 0020), mais l'identité était « légère » : l'en-tête `X-ETL-User`
posé par l'interface elle-même, sans aucun contrôle. Sur un serveur, n'importe qui pouvait se faire passer pour un
autre. Carriole est un logiciel distribué : l'authentification doit convenir à n'importe quelle organisation.

## Décision
1. **Mode** : `etl serve --server` pose `ETL_MODE=server` (hérité par les workers). Écoute sur `ETL_HOST` (0.0.0.0) et
   `ETL_PORT` (8080), flux dans la base, cache gardé selon la rétention, pas de `server.json`. Le poste de bureau ne
   change pas.
2. **Fournisseurs d'identité interchangeables** (`ETL_AUTH`) :
   - `ldap` : le compte de service (ou une connexion anonyme) cherche le compte par un filtre réglable
     (`(uid={username})` par défaut, valeur échappée), puis Carriole se connecte avec le mot de passe saisi (jamais
     vide : un annuaire l'accepterait comme connexion anonyme). Groupes lus par un filtre réglable (`member`,
     `uniqueMember`, `memberUid`), groupe obligatoire facultatif, par son nom ou son DN. `ldaps://` ou StartTLS avec
     vérification du certificat. Bibliothèque `ldap3` (Python pur, LGPL) ;
   - `proxy` : un proxy d'authentification (SSO, OIDC…) transmet le nom (et les groupes) dans des en-têtes réglables ;
     ils ne sont crus que si la connexion vient d'un proxy de confiance (`ETL_TRUSTED_PROXIES`) ;
   - `file` : comptes d'un fichier JSON, mots de passe hachés par scrypt, gérés par `etl users`.
   Sans fournisseur valable, le serveur refuse de démarrer et dit quoi régler.
3. **Sessions** : jeton aléatoire dans un cookie `HttpOnly; SameSite=Strict` (`Secure` derrière HTTPS) ; la base n'en
   garde que l'empreinte SHA-256 ; expiration glissante (`ETL_SESSION_HOURS`, 12 h). Les essais de mot de passe sont
   freinés par compte et par adresse (délai qui double après 5 échecs).
4. **Middleware `ServerGuard`** (ASGI pur, comme `LocalGuard`) : toute route `/api` (sauf santé et connexion) et
   `/tiles` exige une identité ; l'en-tête `X-ETL-User` du navigateur est ignoré ; une écriture dont l'`Origin` n'est
   pas l'hôte est refusée ; `X-Forwarded-For` / `-Proto` ne sont lus que derrière un proxy de confiance.
5. **Administrateurs** : `ETL_ADMINS` (noms et `group:…`, par défaut `group:carriole-admins`), recalculé à chaque
   requête. Eux seuls modifient les réglages de la machine (*Paramètres*, fournisseurs de l'assistant), installent un
   plugin et arrêtent le serveur ; les autres les consultent. La langue de l'interface est propre à chaque
   utilisateur (table `users`). Sur le poste de bureau, l'utilisateur est administrateur de son poste.
6. **Fichiers** : en mode serveur, sans `ETL_READ_ROOTS` / `ETL_WRITE_ROOTS`, aucun fichier n'est accessible (sur le
   poste : aucune restriction, comme avant). Les partages de l'organisation se montent sur le serveur et se déclarent
   comme dossiers autorisés, avec un libellé (`ETL_ROOT_LABELS`).
7. Chaque réglage se lit dans `ETL_<CLÉ>`, sinon dans `settings.json`. Les secrets (mot de passe du compte de service)
   ne se lisent que dans l'environnement ou un fichier (`ETL_LDAP_BIND_PASSWORD_FILE`).

## Conséquences
- L'interface affiche une page de connexion tant qu'il n'y a pas de session ; une réponse 401 y ramène.
- La ligne de commande (`etl run --submit`) reste réservée au backend local.
- Droits sur les flux et les connexions, exécution isolée du code des utilisateurs, quotas et journal d'activité :
  décisions suivantes.
