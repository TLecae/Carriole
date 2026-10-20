import { BrowserWindow, Menu, Notification, Tray, app, dialog, ipcMain, nativeImage, shell } from "electron";
import type { IpcMainInvokeEvent, OpenDialogOptions, SaveDialogOptions } from "electron";
import fs from "node:fs";
import path from "node:path";
import {
  type ServerInfo,
  appDirs,
  call,
  desktopSettings,
  ensureBackend,
  isHealthy,
  loadInstallerDefaults,
  loginUrlOf,
  processAlive,
  stopBackend,
  urlOf,
} from "./backend";
import { applyInstallerLanguage, language, setLanguage, t } from "./i18n";

// ---------------------------------------------------------------------------------------------- emplacements
const repoRoot = path.resolve(__dirname, "..", "..");

/** Python qui exécute le backend : celui embarqué par l'installeur, sinon l'environnement du dépôt (développement). */
function pythonPath(): string {
  if (process.env.ETL_PYTHON) return process.env.ETL_PYTHON;
  if (app.isPackaged) {
    const exe = process.platform === "win32" ? "python.exe" : path.join("bin", "python3");
    return path.join(process.resourcesPath, "runtime", "python", exe);
  }
  return path.join(repoRoot, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
}

function iconPath(): string {
  return app.isPackaged ? path.join(process.resourcesPath, "icon.png") : path.join(__dirname, "..", "build", "icon.png");
}

// ---------------------------------------------------------------------------------------------- état
let win: BrowserWindow | null = null;
let splash: BrowserWindow | null = null;
let tray: Tray | null = null;
let info: ServerInfo | null = null;
let quitting = false;
let quitDecided = false;
let pendingFile: string | null = process.argv.find((a, i) => i > 0 && /\.json$/i.test(a) && fs.existsSync(a)) ?? null;
let trayHintShown = false;
const startHidden = process.argv.includes("--hidden");

const origin = () => (info ? urlOf(info) : "");

function say(message: string): void {
  if (Notification.isSupported()) new Notification({ title: "Carriole", body: message }).show();
}

// ---------------------------------------------------------------------------------------------- fenêtre
function createWindow(): BrowserWindow {
  const w = new BrowserWindow({
    width: 1500,
    height: 950,
    minWidth: 900,
    minHeight: 600,
    show: false,
    title: "Carriole",
    icon: iconPath(),
    backgroundColor: "#ffffff",
    autoHideMenuBar: true, // l'interface a ses propres menus ; la barre native (Alt) garde zoom, rechargement et aide
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
    },
  });
  // Seule l'interface locale s'affiche dans la fenêtre ; un lien externe s'ouvre dans le navigateur (http/https seulement).
  const external = (url: string) => {
    if (/^https?:\/\//i.test(url) && !url.startsWith(origin())) void shell.openExternal(url);
  };
  w.webContents.setWindowOpenHandler(({ url }) => {
    external(url);
    return { action: "deny" };
  });
  w.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith(origin())) {
      event.preventDefault();
      external(url);
    }
  });
  w.webContents.on("did-finish-load", () => {
    if (pendingFile) {
      w.webContents.send("etl:open-file", pendingFile);
      pendingFile = null;
    }
  });
  w.on("ready-to-show", () => {
    if (!startHidden) w.show();
    closeSplash();
  });
  w.on("close", (event) => {
    if (quitting || !tray || !desktopSettings().keep_running) return; // par défaut : la fermeture arrête tout
    event.preventDefault(); // la fenêtre se ferme, l'application reste dans la zone de notification
    w.hide();
    if (!trayHintShown) {
      trayHintShown = true;
      say(t("Carriole continue en arrière-plan (planifications, runs). Clic droit sur l'icône pour quitter."));
    }
  });
  w.on("closed", () => {
    win = null;
  });
  return w;
}

// ---------------------------------------------------------------------------------------------- ouverture
/** Petite fenêtre affichée pendant l'ouverture (démarrage du moteur, chargement de l'interface). */
function createSplash(): BrowserWindow {
  const s = new BrowserWindow({
    width: 340,
    height: 170,
    frame: false,
    resizable: false,
    maximizable: false,
    fullscreenable: false,
    center: true,
    show: false,
    title: "Carriole",
    icon: iconPath(),
    backgroundColor: "#ffffff",
    webPreferences: { contextIsolation: true, sandbox: true, nodeIntegration: false },
  });
  let logo = "";
  try {
    logo = `<img src="data:image/png;base64,${fs.readFileSync(iconPath()).toString("base64")}" alt="">`;
  } catch {
    /* pas d'icône : le texte suffit */
  }
  const html = `<!doctype html><html lang="${language()}"><head><meta charset="utf-8"><title>Carriole</title><style>
    html,body{margin:0;height:100%;font:14px "Segoe UI",system-ui,sans-serif;color:#1f2933;background:#fff;user-select:none}
    body{display:flex;align-items:center;justify-content:center;gap:18px;border:1px solid #d0d7de;box-sizing:border-box;-webkit-app-region:drag}
    img{width:64px;height:64px}
    strong{display:block;font-size:20px;margin-bottom:6px}
    strong span{color:#c98a00}
    p{margin:0;display:flex;align-items:center;gap:8px;color:#52606d}
    i{width:14px;height:14px;border:2px solid #cbd2d9;border-top-color:#1d6fb8;border-radius:50%;animation:r .8s linear infinite}
    @keyframes r{to{transform:rotate(360deg)}}
  </style></head><body>${logo}<div><strong aria-label="Carriole">Carr<span>i/o</span>le</strong><p><i></i>${t("Ouverture en cours…")}</p></div></body></html>`;
  void s.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
  s.once("ready-to-show", () => s.show());
  s.on("closed", () => (splash = null));
  return s;
}

function closeSplash(): void {
  if (splash && !splash.isDestroyed()) splash.destroy();
  splash = null;
}

function showWindow(): void {
  if (!win || win.isDestroyed()) {
    if (!info) return;
    win = createWindow();
    void win.loadURL(loginUrlOf(info));
  }
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
}

async function openFlowFile(file: string): Promise<void> {
  showWindow();
  if (!win) return;
  if (win.webContents.isLoading()) pendingFile = file;
  else win.webContents.send("etl:open-file", file);
}

// ---------------------------------------------------------------------------------------------- menu et zone de notification
async function chooseFlow(): Promise<void> {
  const { canceled, filePaths } = await dialog.showOpenDialog({
    title: t("Ouvrir un flux"),
    filters: [{ name: t("Flux (JSON)"), extensions: ["json"] }, { name: t("Tous les fichiers"), extensions: ["*"] }],
    properties: ["openFile"],
  });
  if (!canceled && filePaths[0]) await openFlowFile(filePaths[0]);
}

function buildMenu(): void {
  const template: Electron.MenuItemConstructorOptions[] = [
    {
      label: t("Fichier"),
      submenu: [
        {
          label: t("Nouveau flux"),
          accelerator: "CmdOrCtrl+N",
          click: () => {
            showWindow();
            win?.webContents.send("etl:new-flow");
          },
        },
        { label: t("Ouvrir un flux…"), accelerator: "CmdOrCtrl+O", click: () => void chooseFlow() },
        { type: "separator" },
        { label: t("Dossier des données de l'application"), click: () => void shell.openPath(appDirs().data) },
        { type: "separator" },
        ...(process.platform === "linux"
          ? []
          : [
              {
                label: t("Démarrer avec Windows"),
                type: "checkbox" as const,
                checked: app.getLoginItemSettings().openAtLogin,
                click: (item: Electron.MenuItem) =>
                  app.setLoginItemSettings({ openAtLogin: item.checked, args: ["--hidden"] }),
              },
              { type: "separator" as const },
            ]),
        { label: t("Quitter"), accelerator: "Alt+F4", click: () => app.quit() },
      ],
    },
    {
      label: t("Affichage"),
      submenu: [
        { role: "reload", label: t("Recharger") },
        { role: "toggleDevTools", label: t("Outils de développement") },
        { type: "separator" },
        { role: "resetZoom", label: t("Taille normale") },
        { role: "zoomIn", label: t("Agrandir") },
        { role: "zoomOut", label: t("Réduire") },
        { role: "togglefullscreen", label: t("Plein écran") },
      ],
    },
    {
      label: t("Aide"),
      submenu: [
        { label: `Carriole ${app.getVersion()}`, enabled: false },
        { label: t("Dossier des journaux"), click: () => void shell.openPath(path.join(appDirs().data, "logs")) },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

async function activeRuns(): Promise<number> {
  if (!info) return 0;
  try {
    const [running, queued] = await Promise.all([
      call<unknown[]>(info, "GET", "/api/runs?status=running&limit=50"),
      call<unknown[]>(info, "GET", "/api/runs?status=queued&limit=50"),
    ]);
    return running.length + queued.length;
  } catch {
    return 0;
  }
}

async function refreshTray(): Promise<void> {
  if (!tray) return;
  const n = await activeRuns();
  tray.setToolTip(n > 0 ? t("Carriole : {{n}} run(s) en cours", { n }) : "Carriole");
}

function buildTrayMenu(): void {
  tray?.setContextMenu(
    Menu.buildFromTemplate([
      { label: t("Ouvrir Carriole"), click: showWindow },
      { label: t("Ouvrir un flux…"), click: () => void chooseFlow() },
      { type: "separator" },
      { label: t("Quitter"), click: () => app.quit() },
    ]),
  );
}

function createTray(): void {
  tray = new Tray(nativeImage.createFromPath(iconPath()));
  tray.setToolTip("Carriole");
  buildTrayMenu();
  tray.on("click", showWindow);
  setInterval(() => void refreshTray(), 15_000).unref();
}

// ---------------------------------------------------------------------------------------------- dialogues natifs
/** Les dialogues ne répondent qu'à la page de Carriole (jamais à un autre site chargé par erreur). */
function trusted(event: IpcMainInvokeEvent): boolean {
  return !!info && (event.senderFrame?.url ?? "").startsWith(origin());
}

interface PickOptions {
  mode: "read" | "write";
  title?: string;
  value?: string;
  defaultName?: string;
  extensions?: string[];
}

function filtersFor(extensions?: string[]): Electron.FileFilter[] {
  const wanted = (extensions ?? []).map((e) => e.replace(/^\./, "")).filter(Boolean);
  const all = { name: t("Tous les fichiers"), extensions: ["*"] };
  if (wanted.length) return [{ name: wanted.map((e) => `*.${e}`).join(" "), extensions: wanted }, all];
  return [all];
}

/** Dossier de départ : celui du chemin du champ s'il existe, sinon le dernier utilisé par le système. */
function startDir(value?: string): string | undefined {
  if (!value) return undefined;
  let dir = path.dirname(value);
  if (/[*?]/.test(dir)) dir = dir.split(/[*?]/)[0];
  return fs.existsSync(dir) ? dir : undefined;
}

function registerIpc(): void {
  ipcMain.handle("etl:pick-file", async (event, options: PickOptions) => {
    if (!trusted(event)) throw new Error("appel refusé");
    const parent = BrowserWindow.fromWebContents(event.sender) ?? undefined;
    const dir = startDir(options.value);
    if (options.mode === "write") {
      const save: SaveDialogOptions = {
        title: options.title ?? t("Choisir où écrire"),
        defaultPath: path.join(dir ?? app.getPath("documents"), options.defaultName ?? path.basename(options.value ?? "")),
        filters: filtersFor(options.extensions),
      };
      const res = parent ? await dialog.showSaveDialog(parent, save) : await dialog.showSaveDialog(save);
      return res.canceled || !res.filePath ? null : res.filePath;
    }
    const open: OpenDialogOptions = {
      title: options.title ?? t("Choisir un fichier"),
      defaultPath: dir,
      filters: filtersFor(options.extensions),
      properties: ["openFile"],
    };
    const res = parent ? await dialog.showOpenDialog(parent, open) : await dialog.showOpenDialog(open);
    return res.canceled ? null : (res.filePaths[0] ?? null);
  });
  ipcMain.handle("etl:pick-folder", async (event, options: PickOptions) => {
    if (!trusted(event)) throw new Error("appel refusé");
    const open: OpenDialogOptions = {
      title: options.title ?? t("Choisir un dossier"),
      defaultPath: startDir(options.value),
      properties: ["openDirectory", "createDirectory"],
    };
    const parent = BrowserWindow.fromWebContents(event.sender);
    const res = parent ? await dialog.showOpenDialog(parent, open) : await dialog.showOpenDialog(open);
    return res.canceled ? null : (res.filePaths[0] ?? null);
  });
  // La page change de langue (paramètres) : menu et zone de notification suivent.
  ipcMain.handle("etl:set-language", (event, code: string) => {
    if (!trusted(event)) throw new Error("appel refusé");
    setLanguage(String(code));
    buildMenu();
    buildTrayMenu();
    void refreshTray();
  });
  ipcMain.handle("etl:show-in-folder", (event, target: string) => {
    if (!trusted(event)) throw new Error("appel refusé");
    shell.showItemInFolder(String(target));
  });
  // Réglages de l'application (fenêtre Paramètres) : démarrage avec Windows, dossiers de l'application, redémarrage.
  ipcMain.handle("etl:get-autostart", (event) => {
    if (!trusted(event)) throw new Error("appel refusé");
    // null : option absente (Electron ne gère le démarrage avec la session que sous Windows et macOS)
    return process.platform === "linux" ? null : app.getLoginItemSettings().openAtLogin;
  });
  ipcMain.handle("etl:set-autostart", (event, enabled: boolean) => {
    if (!trusted(event)) throw new Error("appel refusé");
    app.setLoginItemSettings({ openAtLogin: !!enabled, args: ["--hidden"] });
    buildMenu(); // la case du menu « Fichier » suit
  });
  // Fermer la fenêtre : arrêt complet (par défaut) ou Carriole reste en arrière-plan (runs, planifications).
  ipcMain.handle("etl:get-keep-running", (event) => {
    if (!trusted(event)) throw new Error("appel refusé");
    return desktopSettings().keep_running;
  });
  ipcMain.handle("etl:set-keep-running", async (event, enabled: boolean) => {
    if (!trusted(event) || !info) throw new Error("appel refusé");
    await call(info, "PATCH", "/api/settings", { keep_running: !!enabled });
  });
  ipcMain.handle("etl:open-app-folder", (event, kind: string) => {
    if (!trusted(event)) throw new Error("appel refusé");
    const target = kind === "logs" ? path.join(appDirs().data, "logs") : kind === "data" ? appDirs().data : null;
    if (!target) throw new Error("dossier inconnu");
    return shell.openPath(target);
  });
  ipcMain.handle("etl:relaunch", (event) => {
    if (!trusted(event)) throw new Error("appel refusé");
    app.relaunch(); // le moteur est arrêté ou laissé tourner comme à la fermeture (question posée s'il y a des runs)
    app.quit();
  });
}

// ---------------------------------------------------------------------------------------------- surveillance du moteur
// Le moteur est un processus à part : s'il s'arrête (plantage, mémoire épuisée) ou ne répond plus, il est relancé à la
// même adresse et avec le même jeton ; la page affiche « reconnexion » puis continue, sans perdre le flux en cours.
const WATCH_MS = 5_000;
const HUNG_MS = 90_000; // sans réponse aussi longtemps alors qu'il tourne : bloqué, arrêté puis relancé
let unhealthySince: number | null = null;
let restarting = false;

async function restartBackend(previous: ServerInfo): Promise<void> {
  if (processAlive(previous.pid)) {
    try {
      process.kill(previous.pid); // bloqué : arrêt forcé (ses workers s'arrêtent avec lui, les runs passent « interrompu »)
    } catch {
      /* déjà arrêté */
    }
    const deadline = Date.now() + 10_000;
    while (processAlive(previous.pid) && Date.now() < deadline) await new Promise((r) => setTimeout(r, 200));
  }
  try {
    info = await ensureBackend(pythonPath(), 60_000, { port: previous.port, token: previous.token });
  } catch {
    info = await ensureBackend(pythonPath()); // l'adresse est prise : une autre, la page est rechargée
  }
  if (info.port !== previous.port && win) await win.loadURL(loginUrlOf(info));
}

async function watchBackend(): Promise<void> {
  if (!info || restarting || quitting) return;
  if (await isHealthy(info, 3_000)) {
    unhealthySince = null;
    return;
  }
  unhealthySince ??= Date.now();
  if (processAlive(info.pid) && Date.now() - unhealthySince < HUNG_MS) return; // occupé : on patiente
  restarting = true;
  try {
    await restartBackend(info);
    say(t("Le moteur s'était arrêté : il a été relancé."));
  } catch (err) {
    say(t("Le moteur s'est arrêté et n'a pas pu être relancé : {{error}}", { error: (err as Error).message }));
  } finally {
    restarting = false;
    unhealthySince = null;
  }
}

// ---------------------------------------------------------------------------------------------- démarrage et arrêt
if (!app.requestSingleInstanceLock()) {
  app.quit(); // une instance tourne déjà : elle reçoit « second-instance » et prend le focus
} else {
  app.on("second-instance", (_event, argv) => {
    const file = argv.find((a, i) => i > 0 && /\.json$/i.test(a) && fs.existsSync(a));
    if (file) void openFlowFile(file);
    else showWindow();
  });

  app.whenReady().then(async () => {
    registerIpc();
    buildMenu();
    if (!startHidden) splash = createSplash();
    await loadInstallerDefaults();
    try {
      info = await ensureBackend(pythonPath());
    } catch (err) {
      closeSplash();
      dialog.showErrorBox("Carriole", `${t("Le moteur n'a pas pu démarrer.")}\n\n${(err as Error).message}`);
      app.exit(1);
      return;
    }
    await applyInstallerLanguage(info); // première ouverture : la langue choisie dans l'installeur
    buildMenu();
    createTray();
    win = createWindow();
    await win.loadURL(loginUrlOf(info));
    closeSplash(); // page chargée (elle s'affiche d'elle-même : « ready-to-show »)
    void refreshTray();
    setInterval(() => void watchBackend(), WATCH_MS).unref();
  });

  app.on("window-all-closed", () => {
    // Fermer la dernière fenêtre arrête l'application, sauf réglage « arrière-plan » (zone de notification) : alors
    // seul « Quitter » le fait. La fenêtre d'ouverture qui se ferme n'est pas la dernière fenêtre.
    if (!info) return;
    if (quitting || !tray || !desktopSettings().keep_running) app.quit();
  });

  app.on("before-quit", (event) => {
    quitting = true;
    if (quitDecided) return;
    event.preventDefault();
    void (async () => {
      let stop = true;
      const n = await activeRuns();
      if (n > 0 && info) {
        const { response } = await dialog.showMessageBox({
          type: "question",
          title: "Carriole",
          message: t("{{n}} run(s) en cours ou en attente.", { n }),
          detail: t(
            "Laisser le moteur tourner en arrière-plan permet aux runs de se terminer (l'application ne sera plus visible). L'arrêter les interrompt : ils pourront être repris depuis leur cache.",
          ),
          buttons: [t("Laisser tourner en arrière-plan"), t("Arrêter le moteur"), t("Annuler")],
          defaultId: desktopSettings().keep_running ? 0 : 1,
          cancelId: 2,
        });
        if (response === 2) {
          quitting = false;
          return;
        }
        stop = response === 1;
        if (!stop && process.env.APPIMAGE) {
          // AppImage : le moteur s'exécute depuis le dossier monté de l'application, qui disparaît avec elle. Elle
          // attend donc, sans fenêtre, la fin des runs, puis arrête le moteur.
          for (const w of BrowserWindow.getAllWindows()) w.hide();
          while ((await activeRuns()) > 0) await new Promise((r) => setTimeout(r, 5_000));
          stop = true;
        }
      }
      if (stop && info) await stopBackend(info);
      quitDecided = true;
      app.quit();
    })();
  });
}
