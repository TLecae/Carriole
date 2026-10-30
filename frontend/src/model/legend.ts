import type { ExpressionSpecification } from "maplibre-gl";
import { formatNumber, t } from "../i18n";

/** Couleurs des catégories (valeurs les plus fréquentes d'abord). */
export const CATEGORY_COLORS = ["#1d6fb8", "#d9480f", "#2b8a3e", "#862e9c", "#c2255c", "#e67700", "#0b7285", "#5f3dc4", "#868e96", "#a61e4d"];
/** Rampe des classes d'un attribut numérique, de la plus petite valeur à la plus grande. */
export const RAMP = ["#ffffcc", "#a1dab4", "#41b6c4", "#2c7fb8", "#253494"];
/** Entités sans valeur, ou d'une catégorie hors des plus fréquentes. */
export const NO_VALUE = "#9aa5b1";

export interface LegendItem {
  label: string;
  color: string;
}

/** Coloration d'une couche par un attribut : expression MapLibre (propriété `c` des tuiles) et lignes de la légende. */
export interface Coloring {
  expression: ExpressionSpecification;
  items: LegendItem[];
}

/** Couleurs de `k` classes, réparties sur la rampe. */
export function rampColors(k: number): string[] {
  if (k <= 1) return [RAMP[Math.floor(RAMP.length / 2)]];
  return Array.from({ length: k }, (_, i) => RAMP[Math.round((i * (RAMP.length - 1)) / (k - 1))]);
}

/** Décimales qui distinguent les bornes : aucune pour des entiers, sinon selon le plus petit écart entre deux bornes. */
function decimals(breaks: number[]): number {
  if (breaks.every(Number.isInteger)) return 0;
  const gaps = breaks.slice(1).map((b, i) => b - breaks[i]).filter((g) => g > 0);
  const gap = gaps.length ? Math.min(...gaps) : Math.abs(breaks[0]) || 1;
  return Math.min(6, Math.max(1, Math.ceil(-Math.log10(gap)) + 1));
}

/**
 * Attribut numérique : une couleur par classe. `breaks` va de la plus petite valeur à la plus grande (classes
 * d'effectifs égaux calculées par le serveur) ; une entité sans valeur est grise.
 */
export function classesColoring(breaks: number[], nulls: number): Coloring | null {
  if (!breaks.length) return null;
  const colors = rampColors(Math.max(1, breaks.length - 1));
  const digits = decimals(breaks);
  const fmt = (v: number) => formatNumber(v, digits);
  const items =
    breaks.length === 1
      ? [{ label: fmt(breaks[0]), color: colors[0] }]
      : colors.map((color, i) => ({ label: `${fmt(breaks[i])} – ${fmt(breaks[i + 1])}`, color }));
  if (nulls > 0) items.push({ label: t("Sans valeur"), color: NO_VALUE });
  const steps = breaks.slice(1, -1).flatMap((b, i) => [b, colors[i + 1]]);
  const step = steps.length ? ["step", ["to-number", ["get", "c"]], colors[0], ...steps] : colors[0];
  // une valeur nulle n'est pas écrite dans la tuile : pas de propriété `c`
  return { expression: ["case", ["has", "c"], step, NO_VALUE] as ExpressionSpecification, items };
}

/**
 * Autre attribut : une couleur par valeur. `values` : les plus fréquentes d'abord, une de plus que les couleurs
 * disponibles (pour savoir s'il en reste) ; les autres valeurs et les entités sans valeur sont grises.
 */
export function categoriesColoring(values: { value: unknown; count: number }[]): Coloring | null {
  const shown = values.slice(0, CATEGORY_COLORS.length);
  const labels = [...new Set(shown.filter((v) => v.value !== null).map((v) => String(v.value)))];
  if (!labels.length) return null;
  const items = labels.map((label, i) => ({ label, color: CATEGORY_COLORS[i] }));
  const others = values.length > CATEGORY_COLORS.length;
  const empty = shown.some((v) => v.value === null);
  if (others || empty) items.push({ label: others && empty ? t("Autres valeurs, sans valeur") : others ? t("Autres valeurs") : t("Sans valeur"), color: NO_VALUE });
  const pairs = items.slice(0, labels.length).flatMap((it) => [it.label, it.color]);
  return { expression: ["match", ["to-string", ["get", "c"]], ...pairs, NO_VALUE] as ExpressionSpecification, items };
}

/** Forme du symbole d'une couche dans la légende, selon ses types de géométrie. */
export function shapeOf(types: string[]): "polygon" | "line" | "point" {
  const all = types.join(" ");
  return /POLYGON/.test(all) || !all ? "polygon" : /LINESTRING/.test(all) ? "line" : "point";
}
