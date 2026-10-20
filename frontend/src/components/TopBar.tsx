import { useState } from "react";
import { isEtl } from "../model/graph";
import { goHome, openFlowFileFromHome } from "../navigation";
import { pickPath } from "../platform";
import { useEditor } from "../store/editor";
import { useUi } from "../store/ui";
import { OpenDialog, RunDialog } from "./Dialogs";
import { requestRun } from "../store/run";
import { AppBar, AssistantToggle, Brand, Divider, RunsButton, SettingsButton, Spacer, ToolsMenu } from "./AppBar";
import { BatchDialog } from "./BatchDialog";
import { Icon } from "./Icon";
import { Menu, MenuItem, MenuLabel, MenuSeparator } from "./Menu";
import { t } from "../i18n";

export function TopBar() {
  const s = useEditor();
  const [open, setOpen] = useState(false);
  const runDialog = useUi((u) => u.runRequest);
  const setRunDialog = useUi.getState().setRunRequest;
  const [batchOpen, setBatchOpen] = useState(false);
  const files = useUi((u) => u.flowStorage === "files");

  const picked = s.nodes.filter((n) => n.selected && isEtl(n));
  const one = picked.length === 1 ? picked[0] : null;
  const active = s.run.status === "queued" || s.run.status === "running";
  const empty = s.nodes.length === 0;
  const noParams = s.meta.publishedParams.length === 0;

  const launch = requestRun;

  const openFlow = async () => {
    if (!files) return setOpen(true);
    const path = await pickPath({ mode: "read", title: t("Ouvrir un flux"), extensions: [".json"] });
    if (path) await openFlowFileFromHome(path);
  };

  return (
    <AppBar>
      <Brand onClick={goHome} />
      <Divider />
      <Menu label={t("Fichier")}>
        <MenuItem onSelect={() => s.newFlow()}>{t("Nouveau")}</MenuItem>
        <MenuItem onSelect={() => void openFlow()}>{t("Ouvrir…")}</MenuItem>
        {files && (
          <MenuItem onSelect={() => void s.saveAs()} shortcut="Ctrl+Maj+S">
            {t("Enregistrer sous…")}
          </MenuItem>
        )}
        <MenuSeparator />
        <MenuItem onSelect={goHome}>{t("Retour à la liste des flux")}</MenuItem>
      </Menu>
      <Menu label={t("Édition")}>
        <MenuItem onSelect={s.undo} disabled={!s.past.length} shortcut="Ctrl+Z">
          {t("Annuler")}
        </MenuItem>
        <MenuItem onSelect={s.redo} disabled={!s.future.length} shortcut="Ctrl+Y">
          {t("Rétablir")}
        </MenuItem>
        <MenuSeparator />
        <MenuItem onSelect={s.toggleEnabled} shortcut="Alt+D">
          {t("Activer / désactiver")}
        </MenuItem>
        <MenuSeparator />
        <MenuLabel>{t("Annotations")}</MenuLabel>
        <MenuItem onSelect={() => s.addNote({ x: 80, y: 80 })}>{t("Ajouter une annotation")}</MenuItem>
        <MenuItem onSelect={s.addBookmark}>{t("Créer un signet (sélection)")}</MenuItem>
        <MenuSeparator />
        <MenuLabel>{t("Disposition")}</MenuLabel>
        <MenuItem onSelect={() => s.align("left")}>{t("Aligner à gauche")}</MenuItem>
        <MenuItem onSelect={() => s.align("top")}>{t("Aligner en haut")}</MenuItem>
        <MenuItem onSelect={() => s.align("hcenter")}>{t("Centrer horizontalement")}</MenuItem>
        <MenuItem onSelect={() => s.align("vcenter")}>{t("Centrer verticalement")}</MenuItem>
        <MenuItem onSelect={() => s.align("hdist")}>{t("Répartir horizontalement")}</MenuItem>
        <MenuItem onSelect={() => s.align("vdist")}>{t("Répartir verticalement")}</MenuItem>
      </Menu>
      <Menu label={t("Exécution")}>
        <MenuItem onSelect={() => launch(null, false)} disabled={active || empty}>
          {t("Lancer")}
        </MenuItem>
        <MenuItem onSelect={() => one && launch(one.id, false)} disabled={active || !one} title={t("Lance le flux jusqu'au nœud sélectionné")}>
          {t("Jusqu'au nœud sélectionné")}
        </MenuItem>
        <MenuItem onSelect={() => launch(null, true)} disabled={active || empty || !s.caching} title={t("Recalcule tous les nœuds, sans reprendre ceux du dernier run")}>
          {t("Tout relancer")}
        </MenuItem>
        <MenuItem onSelect={() => setBatchOpen(true)} disabled={active || empty || noParams} title={noParams ? t("Un lot a besoin d'un paramètre publié (panneau du flux)") : t("Un run par fichier d'un dossier ou par valeur")}>
          {t("En lot…")}
        </MenuItem>
        <MenuSeparator />
        <MenuItem onSelect={() => s.setCaching(!s.caching)} checked={s.caching} title={t("Garde les données de chaque nœud : inspecteur, et seuls les nœuds modifiés tournent au run suivant")}>
          {t("Mise en cache des données")}
        </MenuItem>
        <MenuSeparator />
        <MenuItem onSelect={() => void s.cancelRun()} disabled={!active}>
          {t("Arrêter")}
        </MenuItem>
        <MenuSeparator />
        <MenuItem onSelect={() => useUi.getState().setRunsOpen(true)}>{t("Historique des runs")}</MenuItem>
      </Menu>
      <ToolsMenu />
      <Divider />
      {files ? (
        // flux en fichiers : un seul nom, celui du fichier ; « Enregistrer sous… » pour le changer
        <span className="flow-title">
          <strong className="flow-file" aria-label={t("Nom du flux")} data-testid="flow-name" title={s.flowPath ?? t("Pas encore enregistré : Ctrl+S pour choisir le fichier")}>
            {s.meta.name}
          </strong>
          {!s.flowId ? (
            <span className="unsaved-tag" data-testid="flow-unsaved">
              {t("non enregistré")}
            </span>
          ) : (
            s.dirty && (
              <span className="dirty-dot" title={t("Modifications non enregistrées")} aria-label={t("Modifications non enregistrées")}>
                •
              </span>
            )
          )}
        </span>
      ) : (
        <input className="flow-name" aria-label={t("Nom du flux")} value={s.meta.name} onChange={(e) => s.setMeta({ name: e.target.value })} />
      )}
      <button className="primary" onClick={() => void s.save()} disabled={!s.dirty && !!s.flowId} title="Ctrl+S">
        <Icon name="save" /> {t("Enregistrer")}
        {s.dirty ? " •" : ""}
      </button>
      <button className="ghost icon-only" onClick={s.undo} disabled={!s.past.length} title={t("Annuler (Ctrl+Z)")} aria-label={t("Annuler")}>
        <Icon name="undo" />
      </button>
      <button className="ghost icon-only" onClick={s.redo} disabled={!s.future.length} title={t("Rétablir (Ctrl+Y)")} aria-label={t("Rétablir")}>
        <Icon name="redo" />
      </button>
      <Divider />
      <button className="run" onClick={() => launch(null, false)} disabled={active || empty}>
        <Icon name="play" /> {t("Lancer")}
      </button>
      <button
        className={`ghost cache-toggle${s.caching ? " on" : ""}`}
        aria-pressed={s.caching}
        data-testid="cache-toggle"
        onClick={() => s.setCaching(!s.caching)}
        title={
          s.caching
            ? t("Mise en cache activée : les données de chaque nœud sont gardées (inspecteur) et seuls les nœuds modifiés tournent au run suivant. Cliquer pour désactiver.")
            : t("Mise en cache désactivée : tout est recalculé et rien n'est gardé (plus rapide, moins de disque ; pas d'inspecteur). Cliquer pour activer.")
        }
      >
        {t("Cache")}
      </button>
      {active && (
        <button className="danger" onClick={() => void s.cancelRun()}>
          <Icon name="stop" /> {t("Arrêter")}
        </button>
      )}
      <span className={`run-chip ${s.run.status}`} data-testid="run-status">
        {s.run.status === "idle" ? "" : s.run.status}
      </span>
      <Spacer />
      <RunsButton />
      <AssistantToggle />
      <Divider />
      <SettingsButton />
      {open && <OpenDialog onClose={() => setOpen(false)} />}
      {batchOpen && <BatchDialog onClose={() => setBatchOpen(false)} onStarted={() => (setBatchOpen(false), useUi.getState().setRunsOpen(true))} />}
      {runDialog && (
        <RunDialog
          params={s.meta.publishedParams}
          onCancel={() => setRunDialog(null)}
          onRun={(params) => {
            void s.startRun({ target: runDialog.target, fresh: runDialog.fresh, params });
            setRunDialog(null);
          }}
        />
      )}
    </AppBar>
  );
}
