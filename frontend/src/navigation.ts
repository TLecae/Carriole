import { useEditor } from "./store/editor";
import { useUi } from "./store/ui";
import { t } from "./i18n";

const FLOW_HASH = /flow=([A-Za-z0-9_-]+)/;

/** Retour à la liste des flux. L'éditeur garde son contenu : on peut y revenir sans rien perdre. */
export function goHome(): void {
  if (location.hash) history.pushState(null, "", location.pathname + location.search);
  useUi.getState().setView("home");
  void useEditor.getState().loadFlows();
}

/** Ce que l'éditeur contient et qui disparaîtrait en changeant de flux. */
const hasUnsavedWork = () => {
  const s = useEditor.getState();
  return s.dirty && s.nodes.length > 0;
};

const confirmLoss = () => !hasUnsavedWork() || confirm(t("Des modifications non enregistrées seront perdues. Continuer ?"));

/** Ouvre un flux depuis la page d'accueil ; l'adresse de l'éditeur est ajoutée à l'historique (bouton Précédent = accueil). */
export async function openFlowFromHome(id: string): Promise<void> {
  const s = useEditor.getState();
  if (s.flowId === id) return enterEditor(`#flow=${id}`);
  if (!confirmLoss()) return;
  history.pushState(null, "", `#flow=${id}`);
  try {
    await s.openFlow(id);
  } catch {
    history.back();
    s.setNotice({ kind: "error", text: t("Flux introuvable") });
  }
}

/** Flux en fichiers : ouvre un fichier choisi par l'utilisateur (dialogue natif ou explorateur). */
export async function openFlowFileFromHome(path: string): Promise<void> {
  const s = useEditor.getState();
  if (!confirmLoss()) return;
  try {
    await s.openFile(path);
    history.pushState(null, "", location.hash); // l'ouverture a posé l'adresse du flux : Précédent revient à l'accueil
    void s.loadFlows();
  } catch (err) {
    s.setNotice({ kind: "error", text: err instanceof Error ? err.message : t("Ouverture impossible") });
  }
}

export function newFlowFromHome(): void {
  if (!confirmLoss()) return;
  history.pushState(null, "", "#new");
  useEditor.getState().newFlow();
}

/** Reprend l'éditeur tel qu'on l'a laissé. */
export function enterEditor(hash: string): void {
  history.pushState(null, "", hash);
  useUi.getState().setView("editor");
}

/** L'adresse décide de la vue : `#flow=…` et `#new` ouvrent l'éditeur, sinon l'accueil. Appelé au chargement et à Précédent/Suivant. */
export function applyRoute(): void {
  const editor = useEditor.getState();
  const id = FLOW_HASH.exec(location.hash)?.[1];
  if (id) {
    useUi.getState().setView("editor");
    if (editor.flowId !== id) {
      void editor.openFlow(id).catch(() => {
        editor.setNotice({ kind: "error", text: t("Flux introuvable") });
        goHome();
      });
    }
  } else if (location.hash === "#new") {
    useUi.getState().setView("editor");
    // Un flux enregistré est affiché : « nouveau » doit repartir de zéro (avec confirmation s'il y a des modifications).
    if (editor.flowId !== null && confirmLoss()) editor.newFlow();
  } else {
    useUi.getState().setView("home");
    void editor.loadFlows();
  }
}
