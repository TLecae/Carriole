import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { Account } from "../api/types";
import { t } from "../i18n";
import { Modal } from "./Dialogs";

const groupsOf = (text: string): string[] =>
  text
    .split(",")
    .map((g) => g.trim())
    .filter(Boolean);

const fail = (err: unknown) => (err instanceof Error ? err.message : String(err));

/** Comptes locaux du serveur (administrateurs, ADR 0058) : création, groupes, nouveau mot de passe, suppression. */
export function AccountsDialog({ onClose }: { onClose: () => void }) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [editing, setEditing] = useState<string | null>(null); // compte modifié ; "" : nouveau compte
  const [name, setName] = useState("");
  const [display, setDisplay] = useState("");
  const [groups, setGroups] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const load = useCallback(() => {
    api.accounts().then(setAccounts, (err: unknown) => setMessage({ kind: "error", text: fail(err) }));
  }, []);
  useEffect(load, [load]);

  const open = (account: Account | null) => {
    setEditing(account ? account.name : "");
    setName(account?.name ?? "");
    setDisplay(account?.display ?? "");
    setGroups(account?.groups.join(", ") ?? "");
    setPassword("");
    setMessage(null);
  };

  const save = async () => {
    try {
      if (editing === "") {
        await api.createAccount({ name: name.trim(), display, groups: groupsOf(groups), password });
        setMessage({ kind: "ok", text: t("Compte « {{name}} » créé.", { name: name.trim() }) });
      } else if (editing) {
        await api.updateAccount(editing, { display, groups: groupsOf(groups), ...(password ? { password } : {}) });
        setMessage({ kind: "ok", text: t("Compte « {{name}} » enregistré.", { name: editing }) });
      }
      setEditing(null);
      load();
    } catch (err) {
      setMessage({ kind: "error", text: fail(err) });
    }
  };

  const remove = async (account: Account) => {
    if (!confirm(t("Supprimer le compte « {{name}} » ? Ses flux restent, à son nom.", { name: account.name }))) return;
    try {
      await api.deleteAccount(account.name);
      load();
    } catch (err) {
      setMessage({ kind: "error", text: fail(err) });
    }
  };

  return (
    <Modal title={t("Comptes")} onClose={onClose} className="accounts-dialog">
      <p className="muted">{t("Comptes locaux de ce serveur. Les comptes d'un annuaire se gèrent dans l'annuaire.")}</p>
      <table className="accounts-table" data-testid="accounts-table">
        <thead>
          <tr>
            <th>{t("Identifiant")}</th>
            <th>{t("Nom affiché")}</th>
            <th>{t("Groupes")}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {accounts.map((a) => (
            <tr key={a.name}>
              <td className="mono">{a.name}</td>
              <td>{a.display}</td>
              <td>{a.groups.join(", ")}</td>
              <td className="actions">
                <button className="ghost" onClick={() => open(a)} aria-label={t("Modifier {{name}}", { name: a.name })}>
                  {t("Modifier")}
                </button>
                <button className="ghost" onClick={() => void remove(a)} aria-label={t("Supprimer {{name}}", { name: a.name })}>
                  ×
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {editing !== null ? (
        <div className="account-form" data-testid="account-form">
          <label>
            {t("Identifiant")}
            <input value={name} disabled={editing !== ""} onChange={(e) => setName(e.target.value)} autoComplete="off" />
          </label>
          <label>
            {t("Nom affiché")}
            <input value={display} onChange={(e) => setDisplay(e.target.value)} />
          </label>
          <label>
            {t("Groupes (séparés par des virgules)")}
            <input value={groups} onChange={(e) => setGroups(e.target.value)} placeholder="carriole-admins" />
          </label>
          <label>
            {editing === "" ? t("Mot de passe") : t("Nouveau mot de passe (vide : inchangé)")}
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
          </label>
          <div className="modal-actions">
            <button onClick={() => setEditing(null)}>{t("Annuler")}</button>
            <button className="primary" disabled={!name.trim() || (editing === "" && !password)} onClick={() => void save()}>
              {editing === "" ? t("Créer le compte") : t("Enregistrer")}
            </button>
          </div>
        </div>
      ) : (
        <p>
          <button onClick={() => open(null)}>{t("+ Nouveau compte")}</button>
        </p>
      )}
      {message && <p className={message.kind === "ok" ? "ok-text" : "error-text"}>{message.text}</p>}
      <div className="modal-actions">
        <button onClick={onClose}>{t("Fermer")}</button>
      </div>
    </Modal>
  );
}

/** Un utilisateur d'un compte local change son mot de passe (l'actuel est redemandé). */
export function PasswordDialog({ onClose }: { onClose: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const save = async () => {
    if (next !== again) return setError(t("Les deux saisies du nouveau mot de passe diffèrent."));
    try {
      await api.changePassword(current, next);
      setDone(true);
      setError("");
    } catch (err) {
      setError(fail(err));
    }
  };
  return (
    <Modal title={t("Changer mon mot de passe")} onClose={onClose} className="password-dialog">
      {done ? (
        <p className="ok-text">{t("Mot de passe changé.")}</p>
      ) : (
        <div className="account-form">
          <label>
            {t("Mot de passe actuel")}
            <input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" />
          </label>
          <label>
            {t("Nouveau mot de passe")}
            <input type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" />
          </label>
          <label>
            {t("Nouveau mot de passe (encore)")}
            <input type="password" value={again} onChange={(e) => setAgain(e.target.value)} autoComplete="new-password" />
          </label>
        </div>
      )}
      {error && <p className="error-text">{error}</p>}
      <div className="modal-actions">
        <button onClick={onClose}>{t("Fermer")}</button>
        {!done && (
          <button className="primary" disabled={!current || !next} onClick={() => void save()}>
            {t("Changer")}
          </button>
        )}
      </div>
    </Modal>
  );
}
