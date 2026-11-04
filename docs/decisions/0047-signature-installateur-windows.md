# ADR 0047 — Signature de l'installateur Windows par SignPath Foundation

- **Statut** : accepté ; demande à SignPath en cours (2026-11-04)
- **Date** : 2026-11-02

## Contexte
L'installateur Windows n'est pas signé : au téléchargement, Microsoft Defender SmartScreen affiche « Windows a protégé
votre ordinateur », éditeur inconnu. Le public de Carriole n'est pas développeur, et ce message en arrête une partie.

Options étudiées :

- **Azure Artifact Signing** (environ 10 $ par mois) : ouvert aux particuliers des seuls États-Unis et Canada.
- **Certificat OV d'une autorité** (200 à 400 € par an, clé matérielle) : l'éditeur affiché est le mainteneur, mais la
  réputation SmartScreen se construit aussi avec les téléchargements.
- **Microsoft Store** (paquet MSIX) : sans avertissement, mais un autre format de paquet à maintenir.
- **SignPath Foundation** : signature gratuite pour les projets open source. Le certificat est au nom de la fondation.

## Décision
1. **SignPath Foundation** signe l'installateur `Carriole-Setup-<version>.exe`. Ses conditions sont celles du projet :
   licence MIT, aucun composant propriétaire, build par la CI GitHub à partir du dépôt.
2. **Workflow de release** : sur un tag, l'installateur non signé est déposé comme artefact, soumis à SignPath, puis
   le job attend l'approbation manuelle de la demande (jusqu'à 4 h). La release reçoit l'installateur signé.
   - La signature n'a lieu que si la variable `SIGNPATH_ORGANIZATION_ID` et le secret `SIGNPATH_API_TOKEN` existent :
     sans eux, la release reste non signée.
   - La configuration d'artefact (`.signpath/artifact-configuration.xml`, copie de celle gardée par SignPath) n'accepte
     que ce fichier, avec le nom de produit « Carriole » et la version lue dans `desktop/package.json`.
3. **Politique publiée** au moment de la demande : section « Code signing policy » des README (phrase exigée, rôles),
   lien sur la page de téléchargement du site ; les notes de release la portent dès que la variable existe. La
   déclaration de confidentialité exigée est déjà dans les README (section « Privacy »).
4. Les artefacts des workflows ne servent qu'à passer les fichiers au job de release : gardés un jour.

## Conséquences
- L'éditeur affiché devient « SignPath Foundation ». L'avertissement SmartScreen disparaît une fois la réputation du
  certificat acquise, pas forcément dès la première release signée.
- Chaque release demande une approbation dans SignPath.
- Seul l'installateur est signé : `Carriole.exe`, installé par lui, ne l'est pas. SmartScreen ne vérifie que le
  fichier téléchargé.
- Le README et le site indiquent « non signé » jusqu'à la première release signée.
