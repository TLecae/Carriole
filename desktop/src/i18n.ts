import fs from "node:fs";
import path from "node:path";
import { app } from "electron";
import en from "./locales/en.json";
import { type ServerInfo, appDirs, call, registryValue } from "./backend";

/**
 * Textes du processus principal (menus, dialogues, notifications). Le français est la langue source : le texte
 * français est la clé du catalogue (`locales/<langue>.json`), comme dans l'interface et le moteur.
 */
const catalogs: Record<string, Record<string, string>> = { en };
let current = "";

function readSettings(): Record<string, unknown> {
  try {
    return JSON.parse(fs.readFileSync(path.join(appDirs().config, "settings.json"), "utf-8")) as Record<string, unknown>;
  } catch {
    return {};
  }
}

const pick = (code: unknown): string => {
  const short = String(code ?? "").toLowerCase().slice(0, 2);
  return short in catalogs ? short : "fr";
};

/** `ETL_LANG`, sinon le réglage `language` de l'utilisateur, sinon le français. */
export function language(): string {
  if (!current) current = pick(process.env.ETL_LANG || readSettings().language);
  return current;
}

export function setLanguage(code: string): void {
  current = pick(code);
}

export function t(text: string, values: Record<string, string | number> = {}): string {
  const target = catalogs[language()]?.[text] ?? text;
  return target.replace(/\{\{(\w+)\}\}/g, (_match, key: string) => String(values[key] ?? ""));
}

/** Valeur « Language » d'une clé du registre (`Software\Carriole`, ou celle de l'ancien nom), ou null. */
async function registryLanguage(hive: "HKLM" | "HKCU", key = "Carriole"): Promise<string | null> {
  return (await registryValue(hive, key, "Language"))?.toLowerCase() ?? null;
}

/** Langue choisie dans l'installeur (clé du registre de la machine, posée par `build/installer.nsh`). */
export async function installerLanguage(): Promise<string | null> {
  if (process.platform !== "win32" || !app.isPackaged) return null; // l'installeur n'existe que pour l'application installée
  // HKCU et AtelierETL : anciennes installations (par utilisateur, ancien nom)
  return (await registryLanguage("HKLM")) ?? (await registryLanguage("HKCU", "AtelierETL"));
}

/**
 * Première ouverture après une installation : la langue choisie dans l'installeur devient celle de l'application.
 * Elle n'est reprise que si elle a changé depuis la dernière fois (réinstallation dans une autre langue) : un
 * changement fait ensuite dans les paramètres n'est jamais écrasé.
 */
export async function applyInstallerLanguage(info: ServerInfo): Promise<void> {
  const chosen = await installerLanguage();
  if (!chosen || !(chosen in catalogs || chosen === "fr")) return;
  const settings = readSettings();
  if (settings.installer_language === chosen) return;
  try {
    await call(info, "PUT", "/api/preferences/language", { language: chosen, from_installer: true });
    setLanguage(chosen);
  } catch {
    /* l'application démarre dans sa langue actuelle ; le prochain démarrage réessaiera */
  }
}
