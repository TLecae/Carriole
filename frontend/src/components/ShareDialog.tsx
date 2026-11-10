import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Share, Shares } from "../api/types";
import { t } from "../i18n";
import { Modal } from "./Dialogs";

type Kind = "user" | "group" | "*";

const label = (principal: string, users: Map<string, string>): string => {
  if (principal === "*") return t("Tout le monde");
  const [kind, name] = [principal.slice(0, principal.indexOf(":")), principal.slice(principal.indexOf(":") + 1)];
  return kind === "group" ? t("Groupe {{name}}", { name }) : users.get(name) ? `${users.get(name)} (${name})` : name;
};

/**
 * Partage d'un flux ou d'une connexion (mode serveur, ADR 0054) : « lecture » ou « modification » pour un utilisateur,
 * un groupe ou tout le monde. Seul le propriétaire (ou un administrateur) modifie les partages.
 */
export function ShareDialog({ kind, id, title, onClose }: { kind: "flow" | "connection"; id: string; title: string; onClose: () => void }) {
  const [data, setData] = useState<Shares | null>(null);
  const [rows, setRows] = useState<Share[]>([]);
  const [users, setUsers] = useState<{ name: string; display: string }[]>([]);
  const [groups, setGroups] = useState<string[]>([]);
  const [who, setWho] = useState<Kind>("user");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const owner = data?.access === "owner";
  const names = new Map(users.map((u) => [u.name, u.display]));

  useEffect(() => {
    const load = kind === "flow" ? api.flowShares(id) : api.connectionShares(id);
    void load.then(
      (d) => {
        setData(d);
        setRows(d.shares);
      },
      (err: unknown) => setError(String(err instanceof Error ? err.message : err)),
    );
    void api
      .users()
      .then((u) => {
        setUsers(u.users);
        setGroups(u.groups);
      })
      .catch(() => undefined);
  }, [kind, id]);

  const add = () => {
    const principal = who === "*" ? "*" : `${who}:${name.trim()}`;
    if (who !== "*" && !name.trim()) return;
    setRows((r) => [...r.filter((x) => x.principal !== principal), { principal, role: "read" }]);
    setName("");
  };

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      const saved = kind === "flow" ? await api.setFlowShares(id, rows) : await api.setConnectionShares(id, rows);
      setRows(saved.shares);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={t("Partager « {{name}} »", { name: title })} onClose={onClose} className="share-dialog">
      {data && (
        <p className="muted">
          {t("Propriétaire : {{owner}}", { owner: label(`user:${data.owner}`, names) })}
          {kind === "flow" ? ` · ${t("Lecture : ouvrir, lancer, inspecter ses runs. Modification : en plus, enregistrer et arrêter ses runs.")}` : ` · ${t("Lecture : utiliser la connexion dans un flux. Modification : en plus, changer ses réglages.")}`}
        </p>
      )}
      <ul className="share-list" data-testid="share-list">
        {rows.map((row) => (
          <li key={row.principal}>
            <span>{label(row.principal, names)}</span>
            <select
              aria-label={t("Droit de {{who}}", { who: label(row.principal, names) })}
              value={row.role}
              disabled={!owner}
              onChange={(e) => setRows((r) => r.map((x) => (x.principal === row.principal ? { ...x, role: e.target.value as Share["role"] } : x)))}
            >
              <option value="read">{t("Lecture")}</option>
              <option value="write">{t("Modification")}</option>
            </select>
            {owner && (
              <button className="ghost" aria-label={t("Retirer {{who}}", { who: label(row.principal, names) })} onClick={() => setRows((r) => r.filter((x) => x.principal !== row.principal))}>
                ×
              </button>
            )}
          </li>
        ))}
        {!rows.length && <li className="muted">{t("Personne d'autre : seul le propriétaire et les administrateurs y ont accès.")}</li>}
      </ul>
      {owner && (
        <div className="share-add">
          <select aria-label={t("Partager avec")} value={who} onChange={(e) => setWho(e.target.value as Kind)}>
            <option value="user">{t("Utilisateur")}</option>
            <option value="group">{t("Groupe")}</option>
            <option value="*">{t("Tout le monde")}</option>
          </select>
          {who !== "*" && (
            <>
              <input list="share-names" aria-label={t("Nom")} value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} />
              <datalist id="share-names">
                {(who === "user" ? users.map((u) => u.name) : groups).map((n) => (
                  <option key={n} value={n}>
                    {who === "user" ? names.get(n) : n}
                  </option>
                ))}
              </datalist>
            </>
          )}
          <button onClick={add}>{t("Ajouter")}</button>
        </div>
      )}
      {error && <p className="error-text">{error}</p>}
      <div className="modal-actions">
        <button onClick={onClose}>{owner ? t("Annuler") : t("Fermer")}</button>
        {owner && (
          <button className="primary" disabled={busy} onClick={() => void save()}>
            {t("Enregistrer")}
          </button>
        )}
      </div>
    </Modal>
  );
}
