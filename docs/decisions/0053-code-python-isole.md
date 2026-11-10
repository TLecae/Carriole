# ADR 0053 — Code Python des flux isolé en mode serveur

- **Statut** : accepté
- **Date** : 2026-11-10
- **Complète** : ADR 0025 (code en ligne), 0052 (mode serveur)

## Contexte
*PythonScript* exécute une fonction Python écrite dans le flux. Sur un poste, elle tourne avec les droits de
l'utilisateur, comme un plugin : c'est voulu. Sur un serveur partagé, elle tournerait dans le worker, qui voit la base
des métadonnées, la clé des secrets, le cache et les fichiers de tous les utilisateurs : n'importe quel utilisateur
aurait la main sur le serveur, au-delà des dossiers autorisés.

## Décision
1. **Politique** `ETL_USER_CODE` : `allow` (dans le worker ; défaut du poste de bureau), `sandbox` (processus isolé ;
   défaut du mode serveur), `deny` (refusé). Le refus, ou l'absence de bac à sable, est une erreur de configuration :
   le run s'arrête avant de lire des données et l'éditeur le signale dès l'analyse.
2. **Processus isolé** : le code se charge dans un processus fils (`etl_engine.sandbox_child`) qui garde `ctx.state`
   d'un lot à l'autre. Les lots passent par ses entrée et sortie standard au format Arrow IPC ; `ctx.info` /
   `ctx.warning` remontent avec chaque réponse ; `print()` va sur la sortie d'erreur. Erreurs rapportées comme dans
   le worker (ligne du code, erreur de configuration ou d'exécution) ; un fils tué est signalé.
3. **Bac à sable** `ETL_SANDBOX` :
   - `bwrap` (défaut) : bubblewrap (Linux, logiciel libre). Le fils ne voit que le système, Python, ses paquets et
     le dossier d'`etl_engine`, en lecture seule, et un `/tmp` vide ; ni données, ni base, ni cache ; aucun réseau,
     aucune variable d'environnement du serveur, aucun autre processus ; il meurt avec le worker. `sys.path` n'est
     jamais monté en entier : il contient le dossier courant du worker ;
   - `process` : le même protocole sans isolation (développement, Windows) ;
   - toute autre valeur : la commande d'un autre outil d'isolation, à laquelle Carriole ajoute celle du fils.
4. Les plugins restent exécutés dans le worker : seuls les administrateurs les installent (ADR 0052).

## Conséquences
- Sur un serveur, *PythonScript* ne lit ni n'écrit de fichier et n'accède pas au réseau : il transforme les lots
  qu'il reçoit.
- Dans un conteneur, bubblewrap a besoin des espaces de noms utilisateur : le profil seccomp par défaut de Docker les
  refuse (voir le guide du serveur). Sans eux, `ETL_USER_CODE=deny` désactive simplement *PythonScript*.
- Un aller-retour par lot : un peu plus lent que dans le worker, sans effet sur les autres modificateurs.
