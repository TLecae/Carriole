# ADR 0021 — Flux et composants en fichiers

- **Statut** : accepté
- **Date** : 2026-10-11
- **Complète** : [0002](0002-format-json-des-flux.md), [0014](0014-composants.md), [0017](0017-versions-de-composants.md)

## Contexte
L'équipe range ses flux FME sur le serveur de fichiers commun et s'y retrouve. L'atelier de bureau doit faire pareil,
sans serveur de métadonnées partagé : chaque poste a sa base locale (runs, planifications, connexions).

## Décision
- **Un flux est un fichier JSON** (format de l'ADR 0002) rangé où l'on veut. L'application ne garde que la liste des
  flux **récents** (`recent_flows.json`, sans accès disque à l'affichage : un partage injoignable ne bloque rien).
  *Ouvrir…* et *Enregistrer sous…* utilisent les dialogues natifs ; *Retirer de la liste* ne supprime jamais le fichier.
- **Identifiant = chemin** encodé pour une URL (`f` + base64url). Les runs, planifications et liens `#flow=…` retrouvent
  ainsi leur flux. Le flux figé dans un run est inchangé (relancer un vieux run rejoue le flux d'alors).
- **Révision = empreinte du contenu**. L'enregistrement échoue (409) si le fichier a changé sur le disque depuis son
  ouverture (collègue, autre onglet) : rien n'est écrasé ; *Enregistrer sous…* garde le travail.
- **Écriture atomique** (`etl_engine.atomic`) : fichier voisin `.part` puis substitution ; création exclusive
  (`create_only`) pour qu'un second enregistrement du même nouveau fichier échoue au lieu d'écraser.
- **Composants** (`etl_engine.complib`) : fichiers JSON qui déclarent `component`, dans les **dossiers de composants**
  (`ETL_COMPONENTS_DIR`, réglage `component_dirs`) — par exemple un dossier du partage. Versions publiées = fichiers
  voisins immuables `<nom>.v<N>.json` (enveloppe avec note, auteur, date) ; le fichier du composant est le brouillon.
  Les fichiers de versions ne sont jamais pris pour des composants.
- **Deux stockages, une interface** : `FileStore` (poste de bureau, `ETL_FLOW_STORAGE=files`, défini par `etl serve`)
  et `Store` (flux dans la base, mode serveur). `GET /api/version` annonce `flow_storage` ; l'interface adapte l'accueil
  et l'enregistrement. Les routes, l'exécuteur et le planificateur ne voient pas la différence.

## Conséquences
- Pas de verrou d'édition entre collègues (comme FME) : la détection de changement évite d'écraser, pas de co-édition.
- Un flux planifié dont le fichier a disparu ou est sur un lecteur injoignable échoue avec un message dans
  `last_error` ; préférer les chemins UNC (`\\serveur\…`) aux lettres de lecteur pour les planifications.
- Les composants d'une équipe se partagent en pointant `component_dirs` vers le même dossier du partage.
