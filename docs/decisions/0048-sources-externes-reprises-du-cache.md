# ADR 0048 — Sources externes reprises du cache si leurs réglages n'ont pas changé

- **Statut** : accepté
- **Date** : 2026-11-05
- **Revient sur** : ADR 0033 (§3, sources toujours relancées), 0038 (§5, reprise limitée à « Jusqu'au nœud »)

## Contexte
Les lectures de base, de STAC et les appels web étaient relus par chaque *Lancer*, avec tout leur aval, car leur
contenu peut changer sans que le flux change. Un flux à paramètres qui part d'une API web (populations Insee, images
Sentinel-2) recalculait donc presque tous ses nœuds à chaque lancement, même avec les mêmes valeurs : plusieurs
minutes d'appels réseau pour rien, et un serveur sollicité à chaque essai.

## Décision
1. **Une source externe se reprend comme un autre nœud.** `Modifier.reusable = "source"` (lectures de base,
   `DatabaseQuery`, `StacSearch`, `HttpRequest`) : reprise du run précédent si son empreinte (type, réglages, amont)
   n'a pas changé, état *cache*. « Tout relancer » la relit, avec tout son aval. `"partial"`, ancien nom, est encore
   accepté pour les plugins.
2. **Selon les réglages** (`Modifier.reusability`) : seule une lecture est reprise. Un appel web autre que GET, un
   `DatabaseQuery` avec « Autoriser l'écriture » agissent sur le serveur : toujours relancés, avec un sel propre au run
   qui recalcule leur aval. Les écritures, `DatabaseQueryPerFeature`, `RasterWriter`, `SplitToFiles` et les flux lancés
   restent toujours relancés.
3. **Rappel dans l'éditeur.** La description d'un modificateur porte `source` ; sur un tel nœud repris du cache, le
   pied du nœud explique qu'il vient du run précédent et que « Tout relancer » le relit.

## Conséquences
- Relancer un flux à paramètres avec les mêmes valeurs ne relit plus rien ; une valeur changée relit la source qui
  s'en sert (son empreinte change) et recalcule son aval.
- Une base ou une API modifiée entre deux runs n'est pas détectée : c'est à l'utilisateur de choisir « Tout
  relancer ». Le cache désactivé relit tout, comme avant.
- Les runs précédents gardaient une empreinte salée pour ces sources : elles sont relues une fois, au premier run qui
  suit la mise à jour.
