import { useSyncExternalStore } from "react";
import i18next from "i18next";

/**
 * Textes de l'interface. Le français est la langue source : le texte français est la clé (`t("Ouvrir")`), sans
 * catalogue `fr`. Les autres langues ont un catalogue `locales/<code>.json` {texte français: traduction}; une clé
 * absente s'affiche en français. Les valeurs s'écrivent `{{nom}}`.
 */
export const SOURCE_LANGUAGE = "fr";

const catalogs = import.meta.glob<Record<string, string>>("./locales/*.json", { eager: true, import: "default" });
const resources = Object.fromEntries(
  Object.entries(catalogs).map(([path, translation]) => [path.replace(/^.*\/(.+)\.json$/, "$1"), { translation }]),
);

/** Langues dont l'interface a un catalogue (le français est toujours là). */
export const availableLanguages = (): string[] => [SOURCE_LANGUAGE, ...Object.keys(resources).sort()];

const LOCALES: Record<string, string> = { fr: "fr-FR", en: "en-GB" };
const STORAGE_KEY = "etl.language";

function remembered(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

function pick(code: string | undefined | null): string {
  const short = (code ?? "").toLowerCase().split(/[-_]/)[0];
  return availableLanguages().includes(short) ? short : SOURCE_LANGUAGE;
}

void i18next.init({
  lng: pick(remembered()), // le français tant que l'utilisateur n'a pas choisi : la langue du navigateur ne décide pas
  fallbackLng: false,
  resources,
  keySeparator: false,
  nsSeparator: false,
  interpolation: { escapeValue: false },
  returnEmptyString: false,
});

export function t(text: string, values?: Record<string, unknown>): string {
  return i18next.t(text, { ...values, defaultValue: text }) as string;
}

export const language = (): string => i18next.language || SOURCE_LANGUAGE;
export const locale = (): string => LOCALES[language()] ?? language();

const listeners = new Set<() => void>();
i18next.on("languageChanged", () => listeners.forEach((l) => l()));

/** Change la langue de l'affichage (sans toucher au réglage de l'utilisateur : voir `savePreferredLanguage`). */
export async function setLanguage(code: string): Promise<void> {
  const next = pick(code);
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    /* préférence non conservée : le réglage de l'utilisateur fait foi */
  }
  await i18next.changeLanguage(next);
}

/** La racine de l'application se redessine quand la langue change. */
export function useLanguage(): string {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    language,
    language,
  );
}

/** Nombre d'entités court pour les ports et les liaisons : 950, 12,3 k, 1,6 M, 2,1 Mds (le nombre exact va en infobulle). */
export function formatCount(value: number): string {
  const units: [number, string][] = [
    [1e9, t("Mds")],
    [1e6, t("M")],
    [1e3, t("k")],
  ];
  for (const [i, [size, unit]] of units.entries()) {
    const scaled = value / size;
    if (Math.abs(scaled) >= 1) {
      const digits = Math.abs(scaled) < 100 ? 1 : 0;
      const rounded = Number(scaled.toFixed(digits));
      // 999 960 arrondi donne « 1 000 k » : c'est « 1 M »
      if (Math.abs(rounded) >= 1000 && i > 0) return `${formatNumber(Math.sign(rounded))} ${units[i - 1][1]}`;
      return `${formatNumber(rounded, digits)} ${unit}`;
    }
  }
  return formatNumber(value);
}

const numberFormats = new Map<string, Intl.NumberFormat>();
/** Nombre formaté selon la langue (séparateurs de milliers, décimale). */
export function formatNumber(value: number, maximumFractionDigits?: number): string {
  const key = `${locale()}/${maximumFractionDigits ?? ""}`;
  let format = numberFormats.get(key);
  if (!format) {
    format = new Intl.NumberFormat(locale(), maximumFractionDigits === undefined ? undefined : { maximumFractionDigits });
    numberFormats.set(key, format);
  }
  return format.format(value);
}

/** Date et heure à partir d'un horodatage Unix en secondes. */
export function formatDateTime(seconds: number, short = false): string {
  const date = new Date(seconds * 1000);
  return short ? date.toLocaleString(locale(), { dateStyle: "short", timeStyle: "short" }) : date.toLocaleString(locale());
}

export const formatDate = (seconds: number): string => new Date(seconds * 1000).toLocaleDateString(locale());
export const formatTime = (seconds: number): string => new Date(seconds * 1000).toLocaleTimeString(locale());
