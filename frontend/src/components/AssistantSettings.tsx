import { useState } from "react";
import { api } from "../api/client";
import type { AssistantPreset, AssistantProvider, PrivacyMode } from "../api/types";
import { t } from "../i18n";
import { useAssistant } from "../store/assistant";
import { Modal } from "./Dialogs";

interface Draft {
  id: string;
  isNew: boolean;
  kind: "anthropic" | "openai";
  label: string;
  base_url: string;
  model: string;
  privacy: PrivacyMode;
  sample_rows: number;
  api_key: string; // vide : ne pas toucher à la clé enregistrée
  has_key: boolean;
}

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40);

const fromProvider = (p: AssistantProvider): Draft => ({
  id: p.id,
  isNew: false,
  kind: p.kind === "anthropic" ? "anthropic" : "openai",
  label: p.label,
  base_url: p.base_url,
  model: p.model,
  privacy: p.privacy,
  sample_rows: p.sample_rows,
  api_key: "",
  has_key: p.has_key,
});

const fromPreset = (p: AssistantPreset): Draft => ({
  id: p.id,
  isNew: true,
  kind: p.kind,
  label: p.label,
  base_url: p.base_url,
  model: p.model,
  // un modèle sur ce poste peut recevoir des lignes d'exemple ; un fournisseur distant ne reçoit que des schémas
  privacy: /localhost|127\.0\.0\.1/.test(p.base_url) ? "sample" : "schema",
  sample_rows: 5,
  api_key: "",
  has_key: false,
});

/** Fournisseurs de modèles : clé (chiffrée sur ce poste), modèle (liste lue chez le fournisseur) et confidentialité. */
export function AssistantSettings({ onClose }: { onClose: () => void }) {
  const { config, loadConfig } = useAssistant();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const fail = (err: unknown) => setMessage(err instanceof Error ? err.message : String(err));
  const patch = (p: Partial<Draft>) => setDraft((d) => (d ? { ...d, ...p } : d));

  const save = async (d: Draft): Promise<boolean> => {
    try {
      await api.assistantSaveProvider(d.id, {
        kind: d.kind,
        label: d.label,
        base_url: d.base_url,
        model: d.model,
        privacy: d.privacy,
        sample_rows: d.sample_rows,
        ...(d.api_key ? { api_key: d.api_key } : {}),
      });
      await loadConfig();
      patch({ isNew: false, api_key: "", has_key: d.has_key || !!d.api_key });
      return true;
    } catch (err) {
      fail(err);
      return false;
    }
  };

  const loadModels = async (d: Draft) => {
    setBusy(true);
    setMessage("");
    try {
      if (!(await save(d))) return; // la liste se lit avec la clé et l'adresse enregistrées
      setModels((await api.assistantModels(d.id)).models);
      setMessage(t("Connexion réussie."));
    } catch (err) {
      setModels([]);
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  if (draft) {
    const local = /localhost|127\.0\.0\.1/.test(draft.base_url);
    return (
      <Modal title={draft.isNew ? t("Nouveau fournisseur") : draft.label || draft.id} onClose={onClose}>
        <label className="field">
          <span className="field-label">{t("Nom")}</span>
          <input value={draft.label} onChange={(e) => patch({ label: e.target.value, ...(draft.isNew ? { id: slug(e.target.value) || draft.id } : {}) })} />
        </label>
        <label className="field">
          <span className="field-label">{t("Type")}</span>
          <select value={draft.kind} onChange={(e) => patch({ kind: e.target.value as Draft["kind"] })}>
            <option value="anthropic">{t("Anthropic (Claude)")}</option>
            <option value="openai">{t("Compatible OpenAI (OpenAI, OpenRouter, Ollama…)")}</option>
          </select>
        </label>
        <label className="field">
          <span className="field-label">{t("Adresse du fournisseur")}</span>
          <input value={draft.base_url} placeholder={draft.kind === "anthropic" ? t("(par défaut)") : "https://…/v1"} onChange={(e) => patch({ base_url: e.target.value })} />
        </label>
        <label className="field">
          <span className="field-label">{t("Clé d'API")}</span>
          <input
            type="password"
            autoComplete="off"
            value={draft.api_key}
            placeholder={draft.has_key ? t("Clé enregistrée (la remplacer en saisissant une autre)") : local ? t("Aucune clé nécessaire en local") : ""}
            onChange={(e) => patch({ api_key: e.target.value })}
            aria-label={t("Clé d'API")}
          />
          <small className="muted">{t("Chiffrée sur ce poste (coffre de Windows) ; jamais affichée ni écrite dans un flux.")}</small>
        </label>
        <div className="field">
          <span className="field-label">{t("Modèle")}</span>
          <div className="ai-model-row">
            <input list="ai-models" value={draft.model} onChange={(e) => patch({ model: e.target.value })} aria-label={t("Modèle")} />
            <datalist id="ai-models">
              {models.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
            <button type="button" disabled={busy} onClick={() => void loadModels(draft)}>
              {busy ? "…" : t("Lire la liste")}
            </button>
          </div>
          <small className="muted">{t("La liste est lue chez le fournisseur avec votre clé.")}</small>
        </div>
        <label className="field">
          <span className="field-label">{t("Données transmises au modèle")}</span>
          <select value={draft.privacy} onChange={(e) => patch({ privacy: e.target.value as PrivacyMode })} aria-label={t("Données transmises au modèle")}>
            <option value="schema">{t("Schémas seulement (aucune valeur)")}</option>
            <option value="sample">{t("Schémas et lignes d'exemple")}</option>
          </select>
          <small className="muted">{t("Le texte que vous écrivez, le flux ouvert (paramètres et chemins compris) et les messages du journal sont toujours transmis.")}</small>
        </label>
        {draft.privacy === "sample" && (
          <label className="field">
            <span className="field-label">{t("Lignes d'exemple par port")}</span>
            <input type="number" min={1} max={20} value={draft.sample_rows} onChange={(e) => patch({ sample_rows: Math.max(1, Math.min(20, Number(e.target.value) || 1)) })} />
          </label>
        )}
        {message && <p className={message === t("Connexion réussie.") ? "muted" : "error"}>{message}</p>}
        <div className="modal-actions">
          <button onClick={() => (setDraft(null), setMessage(""), setModels([]))}>{t("Retour")}</button>
          <button className="primary" onClick={() => void save(draft).then((ok) => ok && (setDraft(null), setMessage("")))}>
            {t("Enregistrer")}
          </button>
        </div>
      </Modal>
    );
  }

  const unused = (config?.presets ?? []).filter((p) => !config?.providers.some((x) => x.id === p.id));
  return (
    <Modal title={t("Fournisseurs de l'assistant")} onClose={onClose}>
      {config?.providers.length === 0 && <p className="muted">{t("Aucun fournisseur. Ajouter Claude, OpenAI, OpenRouter ou un modèle local (Ollama).")}</p>}
      <ul className="ai-providers">
        {config?.providers.map((p) => (
          <li key={p.id}>
            <label>
              <input type="radio" name="ai-default" checked={config.default === p.id} onChange={() => void api.assistantDefault(p.id).then(loadConfig)} aria-label={t("Utiliser {{name}}", { name: p.label })} />
              <strong>{p.label}</strong> <span className="muted">{p.model || t("(modèle à choisir)")}</span>
            </label>
            <span className="grow" />
            <button onClick={() => (setDraft(fromProvider(p)), setMessage(""))}>{t("Modifier")}</button>
            <button
              onClick={() => confirm(t("Supprimer le fournisseur {{name}} et sa clé ?", { name: p.label })) && void api.assistantDeleteProvider(p.id).then(loadConfig).catch(fail)}
            >
              {t("Supprimer")}
            </button>
          </li>
        ))}
      </ul>
      <div className="ai-presets">
        {unused.map((p) => (
          <button key={p.id} onClick={() => (setDraft(fromPreset(p)), setMessage(""))}>
            + {p.label}
          </button>
        ))}
        <button onClick={() => setDraft({ ...fromPreset({ id: "perso", kind: "openai", label: t("Fournisseur personnalisé"), base_url: "", model: "" }), id: "perso" })}>+ {t("Autre")}</button>
      </div>
      {message && <p className="error">{message}</p>}
      <div className="modal-actions">
        <button onClick={onClose}>{t("Fermer")}</button>
      </div>
    </Modal>
  );
}
