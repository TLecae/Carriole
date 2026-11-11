import { useState } from "react";
import { useAssistant } from "../store/assistant";
import { useSession } from "../store/session";
import { useUi } from "../store/ui";
import { t } from "../i18n";
import { Icon } from "./Icon";
import { AccountsDialog, PasswordDialog } from "./AccountsDialog";
import { AuditDialog } from "./AuditDialog";
import { Menu, MenuItem } from "./Menu";

/** Barre du haut commune à l'accueil et à l'éditeur : menus, zone d'actions, outils de l'application. Le nom de
 * l'application est dans la barre de titre de la fenêtre : la barre ne le répète pas. */
export function AppBar({ children }: { children: React.ReactNode }) {
  return <header className="appbar">{children}</header>;
}

export const Divider = () => <span className="divider" role="separator" aria-orientation="vertical" />;
export const Spacer = () => <span className="grow" />;

/** Retour à l'accueil (liste des flux), en tête de la barre de l'éditeur. */
export function HomeButton({ onClick }: { onClick: () => void }) {
  return (
    <button className="ghost icon-only" onClick={onClick} title={t("Retour à la liste des flux")} aria-label={t("Accueil")}>
      <Icon name="home" size={18} />
    </button>
  );
}

/** Menu « Outils » : ce qui se règle rarement (planifications, connexions aux bases). */
export function ToolsMenu() {
  const ui = useUi.getState();
  const auditable = useSession((s) => s.me?.mode === "server" && s.me.admin);
  const manageAccounts = useSession((s) => s.me?.mode === "server" && s.me.admin && !!s.me.accounts);
  const [audit, setAudit] = useState(false);
  const [accounts, setAccounts] = useState(false);
  return (
    <>
      <Menu label={t("Outils")}>
        <MenuItem onSelect={() => ui.setSchedulesOpen(true)}>{t("Planification")}</MenuItem>
        <MenuItem onSelect={() => ui.setConnectionsOpen(true)}>{t("Connexions")}</MenuItem>
        {manageAccounts && <MenuItem onSelect={() => setAccounts(true)}>{t("Comptes")}</MenuItem>}
        {auditable && <MenuItem onSelect={() => setAudit(true)}>{t("Journal d'activité")}</MenuItem>}
      </Menu>
      {audit && <AuditDialog onClose={() => setAudit(false)} />}
      {accounts && <AccountsDialog onClose={() => setAccounts(false)} />}
    </>
  );
}

export function RunsButton() {
  return (
    <button className="ghost" onClick={() => useUi.getState().setRunsOpen(true)}>
      <Icon name="activity" /> {t("Runs")}
    </button>
  );
}

export function AssistantToggle() {
  const open = useAssistant((a) => a.open);
  return (
    <button aria-pressed={open} className={`ghost${open ? " active" : ""}`} onClick={() => useAssistant.getState().setOpen(!open)} data-testid="open-assistant">
      <Icon name="sparkles" /> {t("Assistant")}
    </button>
  );
}

/** Mode serveur : qui est connecté, et la déconnexion. Rien sur le poste de bureau. */
export function UserMenu() {
  const me = useSession((s) => s.me);
  const [password, setPassword] = useState(false);
  if (me?.mode !== "server") return null;
  return (
    <>
      <Menu label={me.display || me.name} className="user-menu">
        <p className="menu-empty" data-testid="user-identity">
          {me.name}
          {me.admin ? ` · ${t("administrateur")}` : ""}
          {me.groups.length ? <br /> : null}
          {me.groups.length ? t("Groupes : {{groups}}", { groups: me.groups.join(", ") }) : null}
        </p>
        {me.local && <MenuItem onSelect={() => setPassword(true)}>{t("Changer mon mot de passe…")}</MenuItem>}
        {me.logout && <MenuItem onSelect={() => void useSession.getState().logout()}>{t("Se déconnecter")}</MenuItem>}
      </Menu>
      {password && <PasswordDialog onClose={() => setPassword(false)} />}
    </>
  );
}

export function SettingsButton() {
  return (
    <button className="ghost" onClick={() => useUi.getState().setSettingsOpen(true)}>
      <Icon name="settings" /> {t("Paramètres")}
    </button>
  );
}
