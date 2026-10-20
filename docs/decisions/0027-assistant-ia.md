# ADR 0027 — Assistant IA : outils de lecture, propositions validées d'un clic, confidentialité

- **Statut** : accepté
- **Date** : 2026-10-12

## Contexte
Une équipe SIG veut un assistant qui crée un flux à partir d'une description, explique une erreur, écrit un modificateur ou
remplit un `ExecuteSql`. Les données foncières sont sous convention : rien ne doit partir chez un fournisseur sans que
l'utilisateur le sache. Et un modèle ne doit jamais modifier un flux ou exécuter du code sans accord explicite.

## Décisions
- **Paquet `assistant/`** (`etl-assistant`, membre du workspace uv) : fournisseurs, outils, boucle d'agent. Il ne dépend ni
  de l'API ni du magasin : l'API lui fournit un `Backend` (fichiers, runs, données du cache en lecture seule).
- **Fournisseurs** : `anthropic` (SDK officiel, streaming, **mise en cache du prompt** système et des outils) et un adaptateur
  **compatible OpenAI** (SDK `openai` avec `base_url` : OpenAI, OpenRouter, Ollama). Un faux fournisseur scripté sans réseau
  sert aux tests et à la CI. Les messages ont un format interne unique (celui d'Anthropic), converti pour OpenAI. La liste
  des modèles est **lue chez le fournisseur** (`/models`) : aucun identifiant de modèle n'est codé en dur.
- **Boucle d'agent côté serveur** (`POST /api/assistant/chats/{id}/messages`, réponse SSE). Le flux ouvert, le dernier run et
  les références `@` sont envoyés avec chaque message : le serveur ne garde pas d'état de l'éditeur. Les conversations
  sont dans la table locale `chats` (propriétaire = utilisateur Windows) ; les clés d'API dans `assistant_keys`,
  **chiffrées** avec la clé Fernet protégée par le coffre Windows (DPAPI), comme les mots de passe de connexions. La
  clé ne ressort jamais de l'API (« clé enregistrée : oui/non »).
- **Deux familles d'outils.**
  - *Lecture*, exécutés sans confirmation : `list_modifiers`, `describe_modifier`, `inspect_file`, `list_dir`
    (mêmes garde-fous de chemins que les modificateurs), `get_flow`, `analyze_flow`, `get_run_status`, `get_run_logs`,
    `get_port_sample`.
  - *Action*, **jamais exécutés par le serveur** : `propose_flow_edits`, `run_flow`, `write_modifier`,
    `create_component`. Ils produisent une **proposition** validée (Pydantic + `analyze`) que l'interface montre ; l'utilisateur
    l'applique d'un clic. La boucle s'arrête après une proposition valide ; une proposition invalide revient au modèle
    comme erreur d'outil pour qu'il corrige.
- **Modifier le flux** : des opérations (`add_node`, `set_params`, `connect`, `remove_node`…) appliquées à une **copie** ;
  l'interface reçoit le flux résultant, montre la liste des changements et les avertissements de l'analyse, et l'applique
  en **une seule modification annulable** (Ctrl+Z). Elle refuse si le flux a changé depuis la demande.
- **Écrire un modificateur** : la proposition n'est vérifiée que statiquement (syntaxe, classe attendue) et **rien n'est
  exécuté avant le clic**. Au clic, le test pytest du plugin tourne dans un **processus isolé** (dossier temporaire, délai
  de 90 s) ; le plugin n'est copié dans le dossier des plugins que si le test passe, puis le registre est rechargé. Le code
  est affiché pour relecture : il tournera ensuite avec les droits de l'utilisateur, comme tout plugin.
- **Références `@`** : nœud, port d'un run (`@Reader_1.output`) ou fichier (explorateur, dialogue natif, glisser-déposer dans
  l'application de bureau). Chacune est résolue côté serveur en un résumé (schéma, SCR, nombre, emprise).
- **Confidentialité** (réglage par fournisseur, visible en permanence dans le panneau) :
  - « *schéma seulement* » (défaut pour tout fournisseur distant) : noms et types de champs, nombres, SCR ; jamais de valeur
    de donnée ;
  - « *schéma + N lignes* » (défaut pour un fournisseur sur `localhost`, Ollama), N ≤ 20.
  Dans tous les cas partent aussi : le texte de l'utilisateur, le flux ouvert (nœuds, paramètres, chemins) et les messages du
  journal des runs (tronqués à 300 caractères). L'interface le dit dans les réglages.
- **CLI** : `etl ai "…" [--flow f.json] [--out g.json]` interroge le backend local ; le flux modifié n'est écrit que si
  `--out` est donné.

## Conséquences
- Un modèle distant voit les chemins et les paramètres du flux : ne pas y mettre de secret (les mots de passe restent dans
  les connexions, les jetons dans `${env:NOM}`) ; l'invite le rappelle au modèle.
- Le prompt système contient le catalogue complet (≈ 3 000 jetons) : il est mis en cache chez Anthropic.
- Un fournisseur compatible OpenAI sans appels d'outils ne sait pas proposer de modifications (il peut seulement répondre).
- Pas de génération d'images ni de lecture de pièces jointes binaires : seules les références `@` résolues en résumés.
- Un modificateur installé par l'assistant est un fichier dans le dossier des plugins : il reste après la conversation.
