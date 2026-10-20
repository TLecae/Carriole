import { useAssistant } from "../store/assistant";
import { useUi } from "../store/ui";
import { t } from "../i18n";
import { Icon } from "./Icon";
import { Menu, MenuItem } from "./Menu";

/** Barre du haut commune à l'accueil et à l'éditeur : marque, menus, zone d'actions, outils de l'application. */
export function AppBar({ children }: { children: React.ReactNode }) {
  return <header className="appbar">{children}</header>;
}

export const Divider = () => <span className="divider" role="separator" aria-orientation="vertical" />;
export const Spacer = () => <span className="grow" />;

export function Brand({ onClick }: { onClick?: () => void }) {
  // carriole : la charrette, « carry all », et i/o (entrées, sorties) : le « i/o » du nom est celui du logo
  const inner = (
    <>
      <img className="brand-logo" src="/logo.svg" alt="" width={22} height={22} />
      <strong className="brand-name" aria-label="Carriole">
        Carr<span className="brand-io">i/o</span>le
      </strong>
    </>
  );
  return onClick ? (
    <button className="brand ghost" onClick={onClick} title={t("Retour à la liste des flux")} aria-label={t("Accueil")}>
      {inner}
    </button>
  ) : (
    <span className="brand">{inner}</span>
  );
}

/** Menu « Outils » : ce qui se règle rarement (planifications, connexions aux bases). */
export function ToolsMenu() {
  const ui = useUi.getState();
  return (
    <Menu label={t("Outils")}>
      <MenuItem onSelect={() => ui.setSchedulesOpen(true)}>{t("Planification")}</MenuItem>
      <MenuItem onSelect={() => ui.setConnectionsOpen(true)}>{t("Connexions")}</MenuItem>
    </Menu>
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

export function SettingsButton() {
  return (
    <button className="ghost" onClick={() => useUi.getState().setSettingsOpen(true)}>
      <Icon name="settings" /> {t("Paramètres")}
    </button>
  );
}
