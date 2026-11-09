# ADR 0051 — Pause d'un run

- **Statut** : accepté
- **Date** : 2026-11-08
- **Complète** : ADR 0035 (cache gardé tant que le flux est ouvert), 0048 (sources reprises du cache)

## Contexte
*Arrêter* garde les nœuds terminés (repris au run suivant) mais perd le travail des nœuds en cours : une superposition
ou une fusion de plusieurs minutes repart du début. L'utilisateur veut aussi libérer le poste un moment sans perdre
ce travail.

## Décision
1. **Statut** : un run en pause reste `running` (sa place parmi les runs en parallèle, son worker, la protection de
   son cache, la détection d'un worker mort sont inchangées). La demande est la colonne `runs.paused` (schéma v8),
   posée par `POST /runs/{id}/pause`, levée par `/resume` ; refusée (409) hors d'un run en cours ou après un arrêt
   demandé. La fin du run, ou son interruption, la lève.
2. **Moteur** : le worker lit arrêt et pause ensemble chaque seconde (`Store.run_control`), pose un `threading.Event`
   transmis à `run_flow(pause=…)` et publie `run_paused` / `run_resumed`. Les fils s'arrêtent aux points sûrs déjà
   utilisés pour l'arrêt, par `sdk.hold` : `ctx.check_cancel` (lots, paquets, carreaux), lecture d'un lot d'entrée,
   émission d'un lot, démarrage d'un nœud. Une étape d'un seul tenant (requête DuckDB, union d'un très gros groupe)
   va d'abord à son terme. Un arrêt pendant la pause libère les fils aussitôt. Le délai maximal d'un run ne compte
   pas la pause.
3. **Flux modifié pendant la pause** : un run exécute le flux figé à son lancement ; il ne peut pas prendre une
   modification. À la reprise, l'éditeur compare le flux aux signatures du run (`staleness.differsFrom` : nœud
   ajouté, retiré ou modifié). Inchangé : reprise au même point. Changé : le run est arrêté, puis relancé avec
   `reuse_from` sur lui ; l'empreinte de chaque nœud (réglages et amont) décide comme pour tout run : les nœuds
   terminés et inchangés sont repris, les nœuds modifiés et leur aval recalculés, le nœud en cours repart du début.
   Un nœud retiré (une écriture) ne s'exécute donc jamais après la pause.

## Conséquences
- La mémoire du run reste occupée pendant la pause ; un arrêt de Carriole en pause donne un run `interrupted`,
  repris depuis le cache comme après *Arrêter*.
- La ligne de commande (`etl run`) n'a pas de pause : elle n'a pas d'interface pour la demander.
