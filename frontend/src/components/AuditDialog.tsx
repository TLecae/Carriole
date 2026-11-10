import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { AuditEntry } from "../api/types";
import { formatDateTime, t } from "../i18n";
import { Modal } from "./Dialogs";

const ACTIONS: Record<string, () => string> = {
  login: () => t("Connexion"),
  "login.failed": () => t("Connexion refusée"),
  logout: () => t("Déconnexion"),
  "flow.create": () => t("Flux créé"),
  "flow.update": () => t("Flux enregistré"),
  "flow.delete": () => t("Flux supprimé"),
  "flow.share": () => t("Flux partagé"),
  "connection.save": () => t("Connexion à une base enregistrée"),
  "connection.delete": () => t("Connexion à une base supprimée"),
  "connection.share": () => t("Connexion à une base partagée"),
  "run.create": () => t("Run lancé"),
  "run.batch": () => t("Lot lancé"),
  "run.retry": () => t("Run relancé"),
  "run.cancel": () => t("Run arrêté"),
  "run.pause": () => t("Run mis en pause"),
  "run.resume": () => t("Run repris"),
  "schedule.create": () => t("Planification créée"),
  "schedule.update": () => t("Planification modifiée"),
  "schedule.delete": () => t("Planification supprimée"),
  "schedule.run": () => t("Planification lancée"),
  "settings.update": () => t("Réglages modifiés"),
  "assistant.provider": () => t("Fournisseur de l'assistant modifié"),
  "assistant.modifier": () => t("Modificateur installé"),
  "component.publish": () => t("Composant publié"),
  "server.stop": () => t("Serveur arrêté"),
};

const FAMILIES: [string, () => string][] = [
  ["", () => t("Toutes les actions")],
  ["login", () => t("Connexions et déconnexions")],
  ["flow", () => t("Flux")],
  ["run", () => t("Runs")],
  ["connection", () => t("Connexions aux bases")],
  ["schedule", () => t("Planifications")],
  ["settings", () => t("Réglages")],
];

const PAGE = 200;

function detail(e: AuditEntry): string {
  const d = e.detail ?? {};
  if (Array.isArray(d.keys)) return (d.keys as string[]).join(", ");
  if (Array.isArray(d.shares)) return (d.shares as { principal: string; role: string }[]).map((s) => `${s.principal} (${s.role})`).join(", ") || t("aucun partage");
  if (typeof d.name === "string") return d.name;
  if (typeof d.flow === "string") return d.flow;
  if (typeof d.status === "number") return t("code {{status}}", { status: d.status });
  return "";
}

/** Journal d'activité du serveur (administrateurs, ADR 0056) : qui a fait quoi, sur quoi, quand et d'où. */
export function AuditDialog({ onClose }: { onClose: () => void }) {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [user, setUser] = useState("");
  const [family, setFamily] = useState("");
  const [more, setMore] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(
    async (before?: number) => {
      setError("");
      try {
        const page = await api.audit({ user: user.trim(), action: family, before });
        setEntries((current) => (before ? [...current, ...page] : page));
        setMore(page.length === PAGE);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [user, family],
  );

  useEffect(() => {
    const timer = setTimeout(() => void load(), 250);
    return () => clearTimeout(timer);
  }, [load]);

  return (
    <Modal title={t("Journal d'activité")} onClose={onClose} className="audit-dialog">
      <div className="audit-filters">
        <input type="search" placeholder={t("Utilisateur")} aria-label={t("Utilisateur")} value={user} onChange={(e) => setUser(e.target.value)} />
        <select aria-label={t("Actions")} value={family} onChange={(e) => setFamily(e.target.value)}>
          {FAMILIES.map(([value, label]) => (
            <option key={value} value={value}>
              {label()}
            </option>
          ))}
        </select>
      </div>
      <div className="audit-table">
        <table data-testid="audit-table">
          <thead>
            <tr>
              <th>{t("Date")}</th>
              <th>{t("Utilisateur")}</th>
              <th>{t("Action")}</th>
              <th>{t("Objet")}</th>
              <th>{t("Détail")}</th>
              <th>{t("Adresse")}</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.id} className={e.action === "login.failed" ? "audit-failed" : undefined}>
                <td>{formatDateTime(e.ts, true)}</td>
                <td>{e.user}</td>
                <td>{ACTIONS[e.action]?.() ?? e.action}</td>
                <td className="mono">{e.target}</td>
                <td>{detail(e)}</td>
                <td className="mono">{e.ip}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!entries.length && !error && <p className="muted pad">{t("Aucune activité pour ces filtres.")}</p>}
      </div>
      {error && <p className="error-text">{error}</p>}
      <div className="modal-actions">
        {more && <button onClick={() => void load(entries[entries.length - 1]?.id)}>{t("Plus anciens")}</button>}
        <button onClick={onClose}>{t("Fermer")}</button>
      </div>
    </Modal>
  );
}
