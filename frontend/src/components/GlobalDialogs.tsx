import { useUi } from "../store/ui";
import { ConnectionsDialog } from "./ConnectionsDialog";
import { RunsDialog } from "./RunsDialog";
import { SchedulesDialog } from "./SchedulesDialog";
import { SettingsDialog } from "./SettingsDialog";

/** Fenêtres ouvertes depuis la barre du haut (accueil comme éditeur) : affichées une seule fois, par App. */
export function GlobalDialogs() {
  const ui = useUi();
  return (
    <>
      {ui.runsOpen && <RunsDialog onClose={() => ui.setRunsOpen(false)} />}
      {ui.schedulesOpen && <SchedulesDialog onClose={() => ui.setSchedulesOpen(false)} />}
      {ui.connectionsOpen && <ConnectionsDialog onClose={() => ui.setConnectionsOpen(false)} />}
      {ui.settingsOpen && <SettingsDialog onClose={() => ui.setSettingsOpen(false)} />}
    </>
  );
}
