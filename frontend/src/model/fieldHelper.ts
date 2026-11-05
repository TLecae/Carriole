import type { PortRef } from "../api/types";

/**
 * Aide à la saisie des éditeurs (SQL, expressions, filtres) : d'où viennent les attributs proposés, et leurs valeurs.
 * - `connection` : tables et colonnes d'une base, valeurs lues dans la table ;
 * - `inputs` : attributs des entrées du nœud (analyse du flux), valeurs du port amont dans le dernier run ;
 * - `reader` : colonnes du fichier qu'un lecteur lit, valeurs lues dans le fichier.
 */
export interface HelperField {
  name: string;
  type: string;
}

export interface HelperInput {
  port: string; // entrée du nœud (nom de table pour ExecuteSql)
  label: string; // nœud et port amont
  fields: HelperField[];
  ref: PortRef | null; // port amont dans le dernier run, s'il a des données
}

export type HelperSource =
  | { kind: "connection"; connection: string }
  | { kind: "inputs"; inputs: HelperInput[] }
  | { kind: "reader"; node: string; fields: HelperField[] };

const PLAIN = /^[a-z_][a-z0-9_]*$/;

/** Nom d'attribut ou de colonne entre guillemets doubles (sûr quels que soient la casse et les caractères). */
export const quoteName = (name: string): string => `"${name.replace(/"/g, '""')}"`;

/** « schéma.table » tel qu'on l'écrit dans une requête : chaque partie entre guillemets si besoin. */
export const tableName = (name: string): string =>
  name
    .split(".")
    .map((part) => (PLAIN.test(part) ? part : quoteName(part)))
    .join(".");

/** Valeur telle qu'on l'écrit en SQL : texte entre apostrophes, nombre et booléen tels quels, vide : NULL. */
export function literal(value: unknown): string {
  if (value === null || value === undefined) return "NULL";
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : `'${String(value)}'`;
  if (typeof value === "boolean") return value ? "TRUE" : "FALSE";
  return `'${String(value).replace(/'/g, "''")}'`;
}

/** Condition DuckDB « la colonne contient ce texte » (recherche dans les valeurs d'un port de l'inspecteur). */
export const containsWhere = (column: string, text: string): string =>
  text ? `CAST(${quoteName(column)} AS VARCHAR) ILIKE ${literal(`%${text}%`)}` : "";

/** Tables et colonnes pour la complétion de CodeMirror : les entrées par leur nom de port, ou `data` s'il n'y en a
 * qu'une (ses colonnes se complètent alors sans préfixe). */
export function completionSchema(source: HelperSource, tables?: Record<string, string[]>): Record<string, string[]> | undefined {
  if (source.kind === "connection") return tables;
  if (source.kind === "reader") return { data: source.fields.map((f) => f.name) };
  if (source.inputs.length === 1) return { data: source.inputs[0].fields.map((f) => f.name) };
  return Object.fromEntries(source.inputs.map((i) => [i.port, i.fields.map((f) => f.name)]));
}

export interface SchemaTables {
  schema: string; // "" : tables sans schéma
  tables: { name: string; short: string; columns: string[] }[]; // nom complet, nom dans le schéma
}

/** Tables d'une base (« schéma.table » -> colonnes) rangées par schéma, dans l'ordre reçu ; `needle` : seulement
 * celles dont le schéma, le nom ou une colonne le contient (sans la casse). */
export function bySchema(tables: Record<string, string[]>, needle = ""): SchemaTables[] {
  const n = needle.trim().toLowerCase();
  const has = (s: string) => s.toLowerCase().includes(n);
  const groups = new Map<string, SchemaTables>();
  for (const [name, columns] of Object.entries(tables)) {
    const dot = name.indexOf(".");
    const schema = dot > 0 ? name.slice(0, dot) : "";
    const short = dot > 0 ? name.slice(dot + 1) : name;
    if (n && !has(schema) && !has(short) && !columns.some(has)) continue;
    let group = groups.get(schema);
    if (!group) groups.set(schema, (group = { schema, tables: [] }));
    group.tables.push({ name, short, columns });
  }
  return [...groups.values()];
}
