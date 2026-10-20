import { useUi } from "../store/ui";
import { FilePicker } from "./FilePicker";

/** Affiche l'explorateur de fichiers du serveur quand le code demande un chemin hors de l'application de bureau. */
export function PickerHost() {
  const picker = useUi((s) => s.picker);
  const close = useUi((s) => s.closePicker);
  if (!picker) return null;
  return <FilePicker key={picker.title ?? picker.mode} mode={picker.mode} value={picker.value ?? ""} defaultName={picker.defaultName} title={picker.title} extensions={picker.extensions} onClose={() => close(null)} onPick={(path) => close(path)} />;
}
