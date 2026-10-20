import { useEditor } from "./editor";
import { useUi } from "./ui";

/** Lance le flux (jusqu'au nœud `target` s'il est donné) ; s'il a des paramètres publiés, on les demande d'abord. */
export function requestRun(target: string | null, fresh = false): void {
  const s = useEditor.getState();
  if (s.meta.publishedParams.length > 0) useUi.getState().setRunRequest({ target, fresh });
  else void s.startRun({ target, fresh });
}
