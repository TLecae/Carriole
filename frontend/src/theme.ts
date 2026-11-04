import { useSyncExternalStore } from "react";

/** Apparence choisie dans Paramètres → Général : celle du système (par défaut), claire ou sombre. */
export type ThemeChoice = "system" | "light" | "dark";
export type Theme = "light" | "dark";

const KEY = "etl.theme";
const dark = typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
const listeners = new Set<() => void>();

export function themeChoice(): ThemeChoice {
  try {
    const saved = localStorage.getItem(KEY);
    return saved === "light" || saved === "dark" ? saved : "system";
  } catch {
    return "system";
  }
}

/** Thème affiché : le choix de l'utilisateur, ou celui du système s'il n'en a pas fait. */
export function currentTheme(): Theme {
  const choice = themeChoice();
  if (choice !== "system") return choice;
  return dark?.matches ? "dark" : "light";
}

function apply() {
  document.documentElement.dataset.theme = currentTheme();
  listeners.forEach((l) => l());
}

export function setThemeChoice(choice: ThemeChoice) {
  try {
    if (choice === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, choice);
  } catch {
    /* préférence non retenue : elle vaut pour la session */
  }
  apply();
}

/** Pose le thème au démarrage, puis suit le système (passage clair / sombre de Windows ou du bureau Linux). */
export function initTheme() {
  apply();
  dark?.addEventListener("change", apply);
}

const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};

/** Thème affiché, pour les composants qui dessinent eux-mêmes (carte, canevas, éditeur de code). */
export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, currentTheme);
}
