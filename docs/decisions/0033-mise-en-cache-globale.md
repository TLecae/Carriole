# ADR 0033 — Mise en cache globale

- **Statut** : accepté ; point 3 précisé par l'[ADR 0038](0038-carte-fluide-et-run-jusqu-au-noeud.md) (« jusqu'au nœud » reprend les lectures)
- **Date** : 2026-10-13
- **Remplace** : le réglage « Cache » de chaque nœud (selon le run / toujours garder / ne pas garder)

## Contexte
Chaque nœud avait son réglage de cache, et la reprise des nœuds inchangés passait par une commande à part
(« Relancer (cache) »). L'utilisateur demande de gérer le cache au niveau global, pour tout le flux.

## Décision
1. **Un interrupteur** « Cache » à côté de « Lancer » (et dans le menu *Exécution*), retenu d'une session à l'autre
   dans le navigateur, activé par défaut. Il fixe l'option `cache` du run pour tous les nœuds.
2. **Activé : runs partiels.** « Lancer » reprend le dernier run du flux (`reuse_from`) : un nœud dont l'empreinte
   (type, réglages, fichiers lus, amont) n'a pas changé est repris, état *cache* ; les autres tournent. « Tout
   relancer » force un run complet. Un nœud n'est repris que si ses données sont encore sur le disque.
3. **Toujours relancés** (`Modifier.reusable = False`, ou sans sortie) : les écritures (fichier, base, raster, un
   fichier par groupe), les lectures de base, d'API web et de STAC, le SQL, les flux lancés. Leur empreinte inclut
   un sel propre au run : tout leur aval est recalculé aussi, jamais repris avec des données périmées.
4. **Désactivé** : rien n'est gardé ni repris ; l'inspecteur l'explique.
5. Le champ `cache` des nœuds disparaît du format de flux (ignoré à la lecture des anciens flux).

## Conséquences
- Relancer après une petite modification ne recalcule que l'aval de la modification (gain de temps sur les gros flux).
- Une source fichier modifiée est détectée (date et taille dans l'empreinte) ; une base de données ne l'est pas : elle
  est donc toujours relue.
