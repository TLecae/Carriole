import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Share, Shares } from "../api/types";
import { t } from "../i18n";
import { Modal } from "./Dialogs";

const userLabel = (name: string, users: Map<string, string>): string => (users.get(name) ? `${users.get(name)} (${name})` : name);

/**
 * Partage d'un flux ou d'une connexion (mode serveur, ADR 0054) : tout le monde et chaque groupe se cochent ; un
 * utilisateur précis s'ajoute par son identifiant. Chaque accès a son droit : utiliser (lecture) ou modifier.
 * Seul le propriétaire (ou un administrateur) modifie les partages ; les autres les consultent.
 */
export function ShareDialog({ kind, id, title, onClose }: { kind: "flow" | "connection"; id: string; title: string; onClose: () => void }) {
  const [data, setData] = useState<Shares | null>(null);
  const [rows, setRows] = useState<Share[]>([]);
  const [users, setUsers] = useState<{ name: string; display: string }[]>([]);
  const [known, setKnown] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const owner = data?.access === "owner";
  const names = new Map(users.map((u) => [u.name, u.display]));
  const roleOf = new Map(rows.map((r) => [r.principal, r.role]));
  // groupes des comptes, et ceux d'un partage existant dont plus aucun compte ne fait partie (pour pouvoir les décocher)
  const shared = rows.filter((r) => r.principal.startsWith("group:")).map((r) => r.principal.slice(6));
  const groups = [...new Set([...known, ...shared])].sort((a, b) => a.localeCompare(b));
  const people = rows.filter((r) => r.principal.startsWith("user:"));
  const roles: [Share["role"], string][] =
    kind === "connection"
      ? [
          ["read", t("Utiliser")],
          ["write", t("Utiliser et modifier")],
        ]
      : [
          ["read", t("Lecture")],
          ["write", t("Modification")],
        ];

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
        setKnown(u.groups);
      })
      .catch(() => undefined);
  }, [kind, id]);

  const toggle = (principal: string, on: boolean) =>
    setRows((r) => (on ? [...r.filter((x) => x.principal !== principal), { principal, role: "read" }] : r.filter((x) => x.principal !== principal)));
  const setRole = (principal: string, role: Share["role"]) => setRows((r) => r.map((x) => (x.principal === principal ? { ...x, role } : x)));
  const addUser = () => {
    if (name.trim()) toggle(`user:${name.trim()}`, true);
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

  /** Une ligne cochable (tout le monde, un groupe) : case, nom, droit quand elle est cochée. */
  const check = (principal: string, text: string) => {
    const role = roleOf.get(principal);
    return (
      <li key={principal}>
        <label className="share-check">
          <input type="checkbox" checked={!!role} disabled={!owner} onChange={(e) => toggle(principal, e.target.checked)} />
          <span>{text}</span>
        </label>
        {role && roleSelect(principal, text, role)}
      </li>
    );
  };
  const roleSelect = (principal: string, who: string, role: Share["role"]) => (
    <select aria-label={t("Droit de {{who}}", { who })} value={role} disabled={!owner} onChange={(e) => setRole(principal, e.target.value as Share["role"])}>
      {roles.map(([value, text]) => (
        <option key={value} value={value}>
          {text}
        </option>
      ))}
    </select>
  );

  return (
    <Modal title={t("Partager « {{name}} »", { name: title })} onClose={onClose} className="share-dialog">
      {data && (
        <p className="muted">
          {t("Propriétaire : {{owner}}", { owner: userLabel(data.owner, names) })}
          {" · "}
          {kind === "flow"
            ? t("Lecture : ouvrir, lancer, inspecter ses runs. Modification : en plus, enregistrer et arrêter ses runs.")
            : t("Utiliser : choisir la connexion dans un flux. Modifier : en plus, changer ses réglages.")}{" "}
          {t("Le propriétaire et les administrateurs ont toujours accès.")}
        </p>
      )}
      <ul className="share-list" data-testid="share-list">
        {check("*", t("Tout le monde"))}
      </ul>
      <h3 className="share-head">{t("Groupes")}</h3>
      <ul className="share-list" data-testid="share-groups">
        {groups.map((g) => check(`group:${g}`, g))}
        {!groups.length && <li className="muted">{t("Aucun groupe : un groupe existe dès qu'un compte en fait partie (Outils → Comptes).")}</li>}
      </ul>
      <h3 className="share-head">{t("Utilisateurs")}</h3>
      <ul className="share-list" data-testid="share-users">
        {people.map((row) => {
          const who = userLabel(row.principal.slice(5), names);
          return (
            <li key={row.principal}>
              <span>{who}</span>
              {roleSelect(row.principal, who, row.role)}
              {owner && (
                <button className="ghost" aria-label={t("Retirer {{who}}", { who })} onClick={() => toggle(row.principal, false)}>
                  ×
                </button>
              )}
            </li>
          );
        })}
        {!people.length && <li className="muted">{t("Aucun utilisateur ajouté un par un.")}</li>}
      </ul>
      {owner && (
        <div className="share-add">
          <input list="share-names" aria-label={t("Identifiant de l'utilisateur")} value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addUser()} />
          <datalist id="share-names">
            {users.map((u) => (
              <option key={u.name} value={u.name}>
                {u.display}
              </option>
            ))}
          </datalist>
          <button onClick={addUser}>{t("Ajouter")}</button>
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
