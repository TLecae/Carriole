import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { PublishedParam } from "../api/types";
import { useEditor } from "../store/editor";
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

export function RunDialog({ params, onRun, onCancel }: { params: PublishedParam[]; onRun: (p: Record<string, string>) => void; onCancel: () => void }) {
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(params.map((p) => [p.name, p.default])));
  const missing = params.filter((p) => p.required && !values[p.name]);
  return (
    <Modal title={t("Paramètres du run")} onClose={onCancel}>
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
