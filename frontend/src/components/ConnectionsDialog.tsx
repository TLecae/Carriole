import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ConnectionInfo, ConnectionInput, ConnectionTest, DbKind } from "../api/types";
import { DB_KINDS, defaultPort, kindLabel } from "../model/databases";
import { useConnections } from "../store/connections";
import { t } from "../i18n";

const SSL_MODES = ["disable", "allow", "prefer", "require", "verify-ca", "verify-full"];
const EMPTY: ConnectionInput = { kind: "postgres", host: "", port: 5432, dbname: "", user: "", sslmode: "prefer", options: {}, password: "" };

/**
 * Gestionnaire de connexions aux bases (PostgreSQL / PostGIS, Oracle). Le mot de passe part au serveur
 * (chiffré), il n'est jamais renvoyé.
 */
export function ConnectionsDialog({ onClose }: { onClose: () => void }) {
  const { list, refresh } = useConnections();
  const [name, setName] = useState("");
  const [isNew, setIsNew] = useState(true);
  const [form, setForm] = useState<ConnectionInput>(EMPTY);
  const [hasPassword, setHasPassword] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void refresh();
  }, [refresh]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const pick = (c: ConnectionInfo) => {
    setName(c.name);
    setIsNew(false);
    setForm({ kind: c.kind || "postgres", host: c.host, port: c.port, dbname: c.dbname, user: c.user, sslmode: c.sslmode, options: c.options ?? {}, password: null });
    setHasPassword(c.has_password);
    setMessage(null);
  };
  const fresh = () => {
    setName("");
    setIsNew(true);
    setForm(EMPTY);
    setHasPassword(false);
    setMessage(null);
  };
  const set = <K extends keyof ConnectionInput>(key: K, value: ConnectionInput[K]) => setForm((f) => ({ ...f, [key]: value }));
  // nouveau type de base : son port par défaut (sauf port déjà personnalisé)
  const setKind = (kind: DbKind) =>
    setForm((f) => ({
      ...f,
      kind,
      port: DB_KINDS.some((k) => k.port === f.port) ? defaultPort(kind) : f.port,
      options: {},
    }));

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    try {
      await action();
    } catch (e) {
      setMessage({ kind: "error", text: e instanceof Error ? e.message : t("Échec") });
    } finally {
      setBusy(false);
    }
  };
  const save = () =>
    run(async () => {
      const saved = await api.saveConnection(name.trim(), form);
      await refresh();
      pick(saved);
      setMessage({ kind: "ok", text: t("Connexion enregistrée") });
    });
  const test = () =>
    run(async () => {
      const result: ConnectionTest = await api.testConnection(name.trim());
      if (result.ok) {
        const spatial = result.spatial ?? t("sans extension spatiale");
        setMessage({ kind: "ok", text: t("Connexion réussie : {{server}} · {{spatial}}", { server: result.server?.split(",")[0], spatial }) });
      } else {
        setMessage({ kind: "error", text: result.error ?? t("Échec") });
      }
    });
  const remove = () =>
    run(async () => {
      if (!confirm(t("Supprimer la connexion « {{name}} » ? Les flux qui l'utilisent ne fonctionneront plus.", { name }))) return;
      await api.deleteConnection(name);
      await refresh();
      fresh();
    });

  const valid = name.trim() !== "" && form.host.trim() !== "" && form.dbname.trim() !== "";
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal wide" role="dialog" aria-label={t("Connexions")} onMouseDown={(e) => e.stopPropagation()}>
        <h2>{t("Connexions aux bases")}</h2>
        <div className="conn-layout">
          <div className="conn-list" role="list">
            {list.map((c) => (
              <button key={c.name} role="listitem" className={!isNew && c.name === name ? "active" : ""} onClick={() => pick(c)}>
                <strong>{c.name}</strong>
                <small>
                  {kindLabel(c.kind)} · {c.host}/{c.dbname}
                </small>
              </button>
            ))}
            <button className="conn-new" onClick={fresh}>
              {t("+ Nouvelle connexion")}
            </button>
          </div>
          <div className="conn-form">
            <label className="field">
              <span className="field-label">{t("Nom")}</span>
              <input aria-label={t("Nom de la connexion")} value={name} disabled={!isNew} onChange={(e) => setName(e.target.value)} placeholder={t("ma_base")} />
              <span className="field-hint">{t("C'est ce nom qu'on choisit dans les modificateurs.")}</span>
            </label>
            <label className="field">
              <span className="field-label">{t("Type de base")}</span>
              <select aria-label={t("Type de base")} value={form.kind} onChange={(e) => setKind(e.target.value as DbKind)}>
                {DB_KINDS.map((k) => (
                  <option key={k.kind} value={k.kind}>
                    {k.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="field-label">{t("Hôte")}</span>
              <input aria-label={t("Hôte")} value={form.host} onChange={(e) => set("host", e.target.value)} />
            </label>
            <div className="conn-row">
              <label className="field">
                <span className="field-label">{t("Port")}</span>
                <input aria-label={t("Port")} type="number" value={form.port} onChange={(e) => set("port", Number(e.target.value))} />
              </label>
              <label className="field">
                <span className="field-label">{form.kind === "oracle" ? t("Service") : t("Base")}</span>
                <input aria-label={t("Base")} value={form.dbname} placeholder={form.kind === "oracle" ? "ORCLPDB1" : ""} onChange={(e) => set("dbname", e.target.value)} />
              </label>
            </div>
            <div className="conn-row">
              <label className="field">
                <span className="field-label">{t("Utilisateur")}</span>
                <input aria-label={t("Utilisateur de la base")} value={form.user} onChange={(e) => set("user", e.target.value)} />
              </label>
              <label className="field">
                <span className="field-label">{t("Mot de passe")}</span>
                <input
                  aria-label={t("Mot de passe")}
                  type="password"
                  autoComplete="new-password"
                  value={form.password ?? ""}
                  placeholder={hasPassword ? t("•••••• (inchangé)") : ""}
                  onChange={(e) => set("password", e.target.value)}
                />
              </label>
            </div>
            {form.kind === "postgres" && (
              <label className="field">
                <span className="field-label">SSL</span>
                <select aria-label={t("Mode SSL")} value={form.sslmode} onChange={(e) => set("sslmode", e.target.value)}>
                  {SSL_MODES.map((m) => (
                    <option key={m}>{m}</option>
                  ))}
                </select>
              </label>
            )}
            {message && (
              <p role={message.kind === "error" ? "alert" : "status"} className={message.kind === "error" ? "error-text" : "ok-text"}>
                {message.text}
              </p>
            )}
            <div className="modal-actions">
              {!isNew && (
                <button className="danger" disabled={busy} onClick={() => void remove()}>
                  {t("Supprimer")}
                </button>
              )}
              <button disabled={busy || isNew} onClick={() => void test()}>
                {t("Tester")}
              </button>
              <button className="primary" disabled={busy || !valid} onClick={() => void save()}>
                {t("Enregistrer")}
              </button>
            </div>
          </div>
        </div>
        <div className="modal-actions">
          <button onClick={onClose}>{t("Fermer")}</button>
        </div>
      </div>
    </div>
  );
}
