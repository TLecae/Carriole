# ADR 0035 — Cache gardé tant que son flux est ouvert (poste de bureau)

- **Statut** : accepté
- **Date** : 2026-10-13
- **Complète** : ADR 0033 (mise en cache globale) ; la rétention par ancienneté et taille reste celle du mode serveur

## Contexte
Le cache d'un poste atteignait 12 Go (un run du RPG : 1,7 Go), sur un disque presque plein. Il était gardé 14 jours
ou jusqu'à 50 Go, quel que soit l'usage. L'utilisateur demande : un flux fermé ne garde pas son cache, même si le
logiciel plante.

## Décision
1. `etl serve` (poste de bureau) pose `ETL_CACHE_POLICY=session`. Le cache gardé est celui :
   - des runs en attente ou en cours, et de ceux qu'ils reprennent ;
   - du run affiché par l'éditeur d'une page ouverte, qui sert à l'inspecteur et à la reprise des nœuds inchangés.
2. Chaque page signale ce run toutes les 20 s (`POST /api/cache/hold`), et à chaque changement. À sa fermeture, elle
   se retire (`/api/cache/release`, par `navigator.sendBeacon`).
3. Le moteur balaie toutes les 15 s (`release_cache`) : le cache des autres runs terminés est supprimé, le run noté
   purgé (journal et comptes restent).
4. **Plantages** :
   - une page qui ne se signale plus depuis 90 s est oubliée ;
   - un moteur qui démarre (après un plantage aussi) attend 90 s avant de balayer, le temps que les pages ouvertes
     se signalent ;
   - les dossiers sans run connu de plus de 10 min sont supprimés.

   À l'arrêt du moteur, tout ce qui n'est pas en cours est libéré.
5. La reprise d'un run fait des liens physiques vers les fichiers du run repris : supprimer l'ancien run ne touche
   pas le nouveau.

## Conséquences
- Fermer un flux puis le rouvrir : premier run complet (rien à reprendre).
- Un run terminé en arrière-plan, fenêtre fermée : ses écritures sont faites, ses données d'inspection supprimées.
- Les runs de la liste *Runs* autres que celui affiché n'ont plus de données (« données purgées »).
