import type { JsonSchema } from "../api/types";

type Defs = Record<string, JsonSchema>;

/** Suit `$ref` et les `anyOf` « X ou null » de Pydantic pour obtenir le schéma réel d'un champ. */
export function resolveSchema(schema: JsonSchema | undefined, defs: Defs): JsonSchema {
  let s: JsonSchema = schema ?? {};
  for (let i = 0; i < 6; i++) {
    if (s.$ref) {
      const { $ref, ...rest } = s;
      s = { ...defs[$ref.split("/").pop() ?? ""], ...rest };
      continue;
    }
    if (s.anyOf) {
      const real = s.anyOf.filter((x) => x.type !== "null");
      if (real.length === 1) {
        const { anyOf: _anyOf, ...rest } = s;
        s = { ...real[0], ...rest };
        continue;
      }
    }
    break;
  }
  return s;
}

export function typeOf(s: JsonSchema): string {
  return Array.isArray(s.type) ? (s.type.find((t) => t !== "null") ?? "string") : (s.type ?? "string");
}

/** Valeur par défaut d'un champ : celle du schéma, sinon une valeur vide du bon type. */
export function defaultValue(schema: JsonSchema | undefined, defs: Defs): unknown {
  const s = resolveSchema(schema, defs);
  if (s.default !== undefined) return structuredClone(s.default);
  switch (typeOf(s)) {
    case "integer":
    case "number":
      return 0;
    case "boolean":
      return false;
    case "array":
      return [];
    case "object": {
      const out: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(s.properties ?? {})) out[k] = defaultValue(v, defs);
      return out;
    }
    default:
      return s.enum ? s.enum[0] : "";
  }
}

/** Libellé d'un champ : le titre du schéma, sinon le nom du champ lisible. */
export function labelOf(key: string, s: JsonSchema): string {
  if (s.title) return s.title;
  const spaced = key.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export const widgetOf = (s: JsonSchema): string | undefined => s["x-etl-widget"] as string | undefined;

/** Texte comparable : minuscules, sans accents (« Évaluée » et « evaluee » se valent). */
export const normalize = (text: string): string =>
  text
    .normalize("NFD")
    .replace(/\p{M}/gu, "")
    .toLowerCase();

/** Chaque mot de `query` se trouve dans l'un des `texts` (casse et accents ignorés) ; une recherche vide garde tout. */
export function textMatches(query: string, ...texts: (string | undefined)[]): boolean {
  const words = normalize(query).split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = normalize(texts.filter(Boolean).join(" "));
  return words.every((w) => hay.includes(w));
}

/** Le champ `key` répond à la recherche : libellé, nom, description, valeurs proposées. */
export function fieldMatches(key: string, schema: JsonSchema | undefined, defs: Defs, query: string): boolean {
  const s = resolveSchema(schema, defs);
  const item = s.items ? resolveSchema(s.items, defs) : undefined;
  const choices = [...(s.enum ?? []), ...(item?.enum ?? [])].map(String);
  return textMatches(query, labelOf(key, s), key, s.description, ...choices);
}
