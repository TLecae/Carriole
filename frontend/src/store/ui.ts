import { create } from "zustand";
import type { PickOptions } from "../platform";

export type BottomTab = "log" | "issues" | "data";
export type View = "home" | "editor";

/** Demande en cours d'un chemin à l'explorateur de la page (hors application de bureau). */
export interface PickerRequest extends PickOptions {
  resolve: (path: string | null) => void;
}

export const MIN_BOTTOM = 120;
const DEFAULT_BOTTOM = 230;
const HEIGHT_KEY = "etl.bottomHeight";

/** Hauteur maximale du panneau : l'éditeur garde de quoi travailler au-dessus. */
export const maxBottom = () => Math.max(MIN_BOTTOM, window.innerHeight - 180);
const clamp = (h: number) => Math.round(Math.min(maxBottom(), Math.max(MIN_BOTTOM, h)));

function loadHeight(): number {
  try {
    const saved = Number(localStorage.getItem(HEIGHT_KEY));
    if (Number.isFinite(saved) && saved > 0) return clamp(saved);
  } catch {
    /* préférence non lue */
  }
  return DEFAULT_BOTTOM;
}

export const MIN_RIGHT = 260;
const DEFAULT_RIGHT = 340;
const WIDTH_KEY = "etl.rightWidth";

/** Largeur maximale du panneau des propriétés : le canevas garde de quoi travailler. */
export const maxRight = () => Math.max(MIN_RIGHT, window.innerWidth - 220 - 360);
const clampWidth = (w: number) => Math.round(Math.min(maxRight(), Math.max(MIN_RIGHT, w)));

function loadWidth(): number {
  try {
    const saved = Number(localStorage.getItem(WIDTH_KEY));
    if (Number.isFinite(saved) && saved > 0) return clampWidth(saved);
  } catch {
    /* préférence non lue */
  }
  return DEFAULT_RIGHT;
}

interface UiState {
  view: View; // page d'accueil (liste des flux) ou éditeur
  setView: (view: View) => void;
  tab: BottomTab;
  bottomHeight: number; // hauteur du panneau du bas (journal, avertissements, données), réglable à la souris
  rightWidth: number; // largeur du panneau des propriétés (paramètres du nœud), réglable à la souris
  fullscreen: boolean; // le panneau du bas occupe toute la page
  connectionsOpen: boolean; // gestionnaire de connexions (barre du haut ou champ « Connexion »)
  settingsOpen: boolean; // paramètres de l'application : affichés par App, hors de la page qui se redessine
  setSettingsOpen: (open: boolean) => void;
  runsOpen: boolean; // liste des runs
  setRunsOpen: (open: boolean) => void;
  schedulesOpen: boolean; // planifications
  setSchedulesOpen: (open: boolean) => void;
  flowStorage: "files" | "db"; // les flux sont des fichiers (poste de bureau) ou dans la base (serveur)
  setFlowStorage: (mode: "files" | "db") => void;
  picker: PickerRequest | null;
  openPicker: (request: PickerRequest) => void;
  closePicker: (path: string | null) => void;
  setConnectionsOpen: (open: boolean) => void;
  runRequest: { target: string | null; fresh: boolean } | null; // lancement qui attend ses paramètres publiés
  setRunRequest: (request: { target: string | null; fresh: boolean } | null) => void;
  show: (tab: BottomTab) => void;
  setBottomHeight: (height: number, persist?: boolean) => void;
  setRightWidth: (width: number, persist?: boolean) => void;
  setFullscreen: (on: boolean) => void;
}

export const useUi = create<UiState>((set) => ({
  view: /^#(flow=|new)/.test(location.hash) ? "editor" : "home", // l'adresse d'un flux ouvre l'éditeur sans passer par l'accueil
  setView: (view) => set({ view }),
  tab: "log",
  bottomHeight: loadHeight(),
  rightWidth: loadWidth(),
  fullscreen: false,
  connectionsOpen: false,
  settingsOpen: false,
  setSettingsOpen: (settingsOpen) => set({ settingsOpen }),
  runsOpen: false,
  setRunsOpen: (runsOpen) => set({ runsOpen }),
  schedulesOpen: false,
  setSchedulesOpen: (schedulesOpen) => set({ schedulesOpen }),
  flowStorage: "db",
  setFlowStorage: (flowStorage) => set({ flowStorage }),
  picker: null,
  openPicker: (picker) => set({ picker }),
  closePicker: (path) =>
    set((s) => {
      s.picker?.resolve(path);
      return { picker: null };
    }),
  setConnectionsOpen: (connectionsOpen) => set({ connectionsOpen }),
  runRequest: null,
  setRunRequest: (runRequest) => set({ runRequest }),
  show: (tab) => set({ tab }),
  setBottomHeight: (height, persist = true) => {
    const bottomHeight = clamp(height);
    set({ bottomHeight });
    if (!persist) return;
    try {
      localStorage.setItem(HEIGHT_KEY, String(bottomHeight));
    } catch {
      /* préférence non conservée */
    }
  },
  setRightWidth: (width, persist = true) => {
    const rightWidth = clampWidth(width);
    set({ rightWidth });
    if (!persist) return;
    try {
      localStorage.setItem(WIDTH_KEY, String(rightWidth));
    } catch {
      /* préférence non conservée */
    }
  },
  setFullscreen: (fullscreen) => set({ fullscreen }),
}));
