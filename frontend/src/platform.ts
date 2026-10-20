import { useUi } from "./store/ui";

/** Ce que l'application de bureau (Electron) met à disposition de la page : dialogues natifs du système. */
export interface PickOptions {
  mode: "read" | "write";
  title?: string;
  /** Chemin actuel du champ : le dialogue s'ouvre dans son dossier. */
  value?: string;
  /** Nom proposé à l'enregistrement. */
  defaultName?: string;
  /** Extensions proposées, avec le point (ex. ".json"). */
  extensions?: string[];
}

export interface DesktopApi {
  pickFile(options: PickOptions): Promise<string | null>;
  pickFolder(options: PickOptions): Promise<string | null>;
  showInFolder(path: string): Promise<void>;
  setLanguage(code: string): Promise<void>;
  getAutoStart?(): Promise<boolean | null>; // null : option absente (Linux)
  setAutoStart?(enabled: boolean): Promise<void>;
  getKeepRunning?(): Promise<boolean>;
  setKeepRunning?(enabled: boolean): Promise<void>;
  openAppFolder?(kind: "data" | "logs"): Promise<string>;
  relaunch?(): Promise<void>;
  pathForFile?(file: File): string;
  onOpenFile(callback: (path: string) => void): void;
  onNewFlow(callback: () => void): void;
}

declare global {
  interface Window {
    etlDesktop?: DesktopApi;
  }
}

export const isDesktop = () => typeof window !== "undefined" && !!window.etlDesktop;

/**
 * Demande un chemin à l'utilisateur : dialogue natif dans l'application de bureau (les vrais chemins, partages réseau
 * compris), sinon l'explorateur de fichiers du serveur dans la page. `null` si l'utilisateur annule.
 */
/** Choisit un dossier : dialogue natif dans l'application de bureau ; `null` ailleurs (le chemin se saisit à la main). */
export function pickFolder(title: string, value?: string): Promise<string | null> {
  return window.etlDesktop ? window.etlDesktop.pickFolder({ mode: "read", title, value }) : Promise.resolve(null);
}

export function pickPath(options: PickOptions): Promise<string | null> {
  if (window.etlDesktop) return window.etlDesktop.pickFile(options);
  return new Promise((resolve) => useUi.getState().openPicker({ ...options, resolve }));
}
