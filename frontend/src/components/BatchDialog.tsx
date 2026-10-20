import { useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import { t } from "../i18n";
import { toFlowDoc } from "../model/graph";
import { pickPath } from "../platform";
import { useEditor } from "../store/editor";
import { Modal } from "./Dialogs";

type Mode = "pattern" | "values";

/**
 * Lancer le flux en lot : un run par fichier d'un motif (`D:\dossier\*.shp`) ou par valeur d'une liste. Chaque valeur
 * est donnée au paramètre publié choisi ; les runs sont regroupés dans la liste des runs.
 */
export function BatchDialog({ onClose, onStarted }: { onClose: () => void; onStarted: () => void }) {
  const params = useEditor((s) => s.meta.publishedParams);
  const [param, setParam] = useState(params.find((p) => p.type === "path")?.name ?? params[0]?.name ?? "");
  const [mode, setMode] = useState<Mode>("pattern");
  const [pattern, setPattern] = useState("");
  const [text, setText] = useState("");
  const [found, setFound] = useState<string[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const values = () => text.split(/\r?\n/).map((v) => v.trim()).filter(Boolean);
  const request = () => {
    const { meta, nodes, edges, flowId } = useEditor.getState();
    return { flow: toFlowDoc(meta, nodes, edges), flow_id: flowId, param, ...(mode === "pattern" ? { pattern } : { values: values() }) };
  };

  // aperçu : le serveur dit quels fichiers le motif trouve (alias de chemins compris)
  useEffect(() => {
    setFound(null);
    setError("");
    if (!param || (mode === "pattern" ? !pattern.trim() : values().length === 0)) return;
    const timer = setTimeout(() => {
      api
        .previewBatch(request())
        .then((r) => setFound(r.values))
        .catch((e: unknown) => setError(e instanceof ApiError ? e.message : String(e)));
    }, 400);
    return () => clearTimeout(timer);
  }, [param, mode, pattern, text]); // eslint-disable-line react-hooks/exhaustive-deps

  const browse = async () => {
    const path = await pickPath({ mode: "read", value: pattern, title: t("Choisir un fichier du lot") });
    if (path) setPattern(path);
  };

  const start = async () => {
    setBusy(true);
    setError("");
    try {
      await useEditor.getState().startBatch({ param, ...(mode === "pattern" ? { pattern } : { values: values() }) });
      onStarted();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={t("Lancer en lot")} onClose={onClose} className="wide">
      <p className="muted">{t("Un run par fichier ou par valeur. Les autres paramètres gardent leur valeur par défaut.")}</p>
      <label className="field">
        <span className="field-label">{t("Paramètre qui reçoit chaque valeur")}</span>
        <select aria-label={t("Paramètre du lot")} value={param} onChange={(e) => setParam(e.target.value)}>
          {params.map((p) => (
            <option key={p.name} value={p.name}>
              {p.label || p.name}
            </option>
          ))}
        </select>
      </label>
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={mode === "pattern"} onClick={() => setMode("pattern")}>
          {t("Fichiers d'un motif")}
        </button>
        <button role="tab" aria-selected={mode === "values"} onClick={() => setMode("values")}>
          {t("Liste de valeurs")}
        </button>
      </div>
      {mode === "pattern" ? (
        <label className="field">
          <span className="field-label">{t("Motif de fichiers")}</span>
          <div className="path-field">
            <input type="text" aria-label={t("Motif de fichiers")} value={pattern} placeholder={t("D:/dossier/*.shp")} onChange={(e) => setPattern(e.target.value)} />
            <button type="button" onClick={() => void browse()}>
              {t("Parcourir…")}
            </button>
          </div>
          <span className="field-hint">{t("* remplace des lettres, ** descend dans les sous-dossiers.")}</span>
        </label>
      ) : (
        <label className="field">
          <span className="field-label">{t("Une valeur par ligne")}</span>
          <textarea rows={6} aria-label={t("Valeurs du lot")} value={text} onChange={(e) => setText(e.target.value)} />
        </label>
      )}
      {found && (
        <p className="ok-text" role="status" data-testid="batch-count">
          {t("{{count}} run(s) seront lancés.", { count: found.length })}
          {found.length > 0 && <small className="muted"> {t("Par exemple : {{first}}", { first: found.slice(0, 3).join(" ; ") })}</small>}
        </p>
      )}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      <div className="modal-actions">
        <button onClick={onClose}>{t("Annuler")}</button>
        <button className="primary" disabled={busy || !found || found.length === 0} onClick={() => void start()}>
          {t("Lancer le lot")}
        </button>
      </div>
    </Modal>
  );
}
