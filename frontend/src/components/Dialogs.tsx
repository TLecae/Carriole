import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { PublishedParam } from "../api/types";
import { useEditor } from "../store/editor";
import { useUi } from "../store/ui";
import { formatDateTime, t } from "../i18n";

export function Modal({ title, onClose, children, className = "" }: { title: string; onClose: () => void; children: React.ReactNode; className?: string }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className={`modal ${className}`.trim()} role="dialog" aria-label={title} onMouseDown={(e) => e.stopPropagation()}>
        <h2>{title}</h2>
        {children}
      </div>
    </div>
  );
}

/** Nom du flux changé avant d'enregistrer : nouveau flux (choix par défaut, l'original reste tel quel) ou renommage. */
export function RenameHost() {
  const ask = useUi((s) => s.renameAsk);
  const close = useUi((s) => s.closeRename);
  const files = useUi((s) => s.flowStorage === "files");
  if (!ask) return null;
  return (
    <Modal title={t("Nom du flux modifié")} onClose={() => close(null)}>
      <p>{t("« {{previous}} » devient « {{name}} ».", { previous: ask.previous, name: ask.name })}</p>
      <p className="muted">
        {files
          ? t("Nouveau fichier : écrit dans le même dossier, l'original reste tel quel. Renommer : le fichier change de nom.")
          : t("Nouveau flux : l'original reste tel quel. Renommer : le flux change de nom.")}
      </p>
      <div className="modal-actions">
        <button onClick={() => close(null)}>{t("Annuler")}</button>
        <button onClick={() => close("rename")}>{files ? t("Renommer le fichier") : t("Renommer le flux")}</button>
        <button className="primary" autoFocus onClick={() => close("new")}>
          {files ? t("Créer un nouveau fichier") : t("Créer un nouveau flux")}
        </button>
      </div>
    </Modal>
  );
}

export function OpenDialog({ onClose }: { onClose: () => void }) {
  const flows = useEditor((s) => s.flows);
  const { loadFlows, openFlow } = useEditor.getState();
  useEffect(() => void loadFlows(), [loadFlows]);
  return (
    <Modal title={t("Ouvrir un flux")} onClose={onClose}>
      {flows.length === 0 && <p className="muted">{t("Aucun flux enregistré.")}</p>}
      <ul className="flow-list">
        {flows.map((f) => (
          <li key={f.id}>
            <button
              type="button"
              className="flow-open"
              onClick={() => {
                void openFlow(f.id).then(() => history.replaceState(null, "", `#flow=${f.id}`));
                onClose();
              }}
            >
              <strong>{f.name}</strong>
              <small>
                {f.owner} · {formatDateTime(f.updated_at)}
              </small>
            </button>
            <button
              type="button"
              className="remove"
              aria-label={t("Supprimer {{name}}", { name: f.name })}
              onClick={async () => {
                if (confirm(t("Supprimer le flux « {{name}} » ?", { name: f.name }))) {
                  await api.deleteFlow(f.id);
                  await loadFlows();
                }
              }}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
      <div className="modal-actions">
        <button onClick={onClose}>{t("Fermer")}</button>
      </div>
    </Modal>
  );
}

/**
 * Valeurs des paramètres publiés avant un lancement. Proposées : celles du dernier run (`last`), sinon les valeurs
 * par défaut. Revenir aux valeurs par défaut à chaque fois changeait l'empreinte des nœuds qui s'en servent, et le run
 * recalculait ce que le cache aurait repris.
 */
export function RunDialog({ params, last, onRun, onCancel }: { params: PublishedParam[]; last?: Record<string, string>; onRun: (p: Record<string, string>) => void; onCancel: () => void }) {
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(params.map((p) => [p.name, last?.[p.name] ?? p.default])));
  const missing = params.filter((p) => p.required && !values[p.name]);
  const fromLast = params.some((p) => last?.[p.name] !== undefined && last[p.name] !== p.default);
  return (
    <Modal title={t("Paramètres du run")} onClose={onCancel}>
      {fromLast && <p className="muted">{t("Valeurs du dernier run : inchangées, les nœuds qui s'en servent sont repris du cache.")}</p>}
      {params.map((p) => (
        <label className="field" key={p.name}>
          <span className="field-label">{p.label || p.name}{p.required ? " *" : ""}</span>
          <input
            type={p.type === "number" ? "number" : "text"}
            value={values[p.name] ?? ""}
            onChange={(e) => setValues({ ...values, [p.name]: e.target.value })}
          />
        </label>
      ))}
      <div className="modal-actions">
        <button onClick={onCancel}>{t("Annuler")}</button>
        <button className="primary" disabled={missing.length > 0} onClick={() => onRun(values)}>
          {t("Lancer")}
        </button>
      </div>
    </Modal>
  );
}
