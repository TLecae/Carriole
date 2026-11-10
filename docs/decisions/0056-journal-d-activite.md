# ADR 0056 — Journal d'activité du mode serveur

- **Statut** : accepté
- **Date** : 2026-11-10
- **Complète** : ADR 0052 (mode serveur), 0054 (droits et partage)

## Contexte
Sur un serveur partagé, un administrateur doit pouvoir répondre à « qui a supprimé ce flux ? », « qui a partagé
cette connexion ? », « d'où viennent ces échecs de connexion ? ». Les journaux techniques (`server.log`) ne le disent
pas de façon lisible.

## Décision
1. Table `audit` (schéma v11) : date, utilisateur, action (code stable : `flow.update`, `connection.share`,
   `login.failed`…), objet (identifiant ou nom), détail, adresse du client (celle que transmet un proxy de confiance).
2. `AuditMiddleware` (ASGI pur, sous `ServerGuard`) suit les routes d'écriture listées par le nom de leur fonction :
   chaque succès ajoute une ligne, et chaque échec de connexion. Les lectures ne sont pas consignées. Le détail ne
   garde jamais de mot de passe ni le contenu d'un flux : le nom d'un flux, les clés de réglages modifiées, les
   partages. Une écriture du journal qui échoue n'empêche pas l'action.
3. Consultation : `GET /api/audit` (administrateurs ; filtres par utilisateur et famille d'actions, pages) et
   *Outils → Journal d'activité* dans l'interface.
4. Rétention : `ETL_AUDIT_DAYS` (365 jours ; 0 : sans limite). Sur le poste de bureau, pas de journal.

## Conséquences
- Une nouvelle route d'écriture s'ajoute à `etl_api.audit.ACTIONS` pour être suivie.
