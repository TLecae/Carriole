// Chargé dans la page avec le « bac à sable » activé : seul le module `electron` est disponible ici.
import { contextBridge, ipcRenderer, webUtils } from "electron";

interface PickOptions {
  mode: "read" | "write";
  title?: string;
  value?: string;
  defaultName?: string;
  extensions?: string[];
}

// Les demandes du menu peuvent arriver avant que la page soit prête (fichier passé au démarrage) : elles sont mises
// en attente jusqu'à ce que la page s'abonne.
type Handler = (path: string) => void;
let openHandler: Handler | null = null;
let newHandler: (() => void) | null = null;
const waiting: ({ kind: "open"; path: string } | { kind: "new" })[] = [];
ipcRenderer.on("etl:open-file", (_event, path: string) => (openHandler ? openHandler(path) : waiting.push({ kind: "open", path })));
ipcRenderer.on("etl:new-flow", () => (newHandler ? newHandler() : waiting.push({ kind: "new" })));

const drain = () => {
  for (const item of waiting.splice(0)) {
    if (item.kind === "open") openHandler?.(item.path);
    else newHandler?.();
  }
};

// L'interface (frontend/src/platform.ts) utilise ces dialogues natifs à la place de l'explorateur du serveur.
contextBridge.exposeInMainWorld("etlDesktop", {
  pickFile: (options: PickOptions): Promise<string | null> => ipcRenderer.invoke("etl:pick-file", options),
  pickFolder: (options: PickOptions): Promise<string | null> => ipcRenderer.invoke("etl:pick-folder", options),
  showInFolder: (path: string): Promise<void> => ipcRenderer.invoke("etl:show-in-folder", path),
  // La page change de langue : le menu et la zone de notification (processus principal) la suivent.
  setLanguage: (code: string): Promise<void> => ipcRenderer.invoke("etl:set-language", code),
  // Fenêtre Paramètres : démarrage avec Windows, dossiers de l'application, redémarrage de l'application.
  getAutoStart: (): Promise<boolean | null> => ipcRenderer.invoke("etl:get-autostart"),
  setAutoStart: (enabled: boolean): Promise<void> => ipcRenderer.invoke("etl:set-autostart", enabled),
  getKeepRunning: (): Promise<boolean> => ipcRenderer.invoke("etl:get-keep-running"),
  setKeepRunning: (enabled: boolean): Promise<void> => ipcRenderer.invoke("etl:set-keep-running", enabled),
  openAppFolder: (kind: "data" | "logs"): Promise<string> => ipcRenderer.invoke("etl:open-app-folder", kind),
  relaunch: (): Promise<void> => ipcRenderer.invoke("etl:relaunch"),
  // Chemin réel d'un fichier glissé dans la page (l'assistant le joint à la question).
  pathForFile: (file: File): string => webUtils.getPathForFile(file),
  // Menu et zone de notification : la page ouvre le flux (avec sa confirmation s'il y a des modifications non enregistrées).
  onOpenFile: (callback: Handler): void => {
    openHandler = callback;
    drain();
  },
  onNewFlow: (callback: () => void): void => {
    newHandler = callback;
    drain();
  },
});
