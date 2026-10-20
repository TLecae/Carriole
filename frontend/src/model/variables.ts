import type { AppNode, FlowMeta } from "./graph";
import { isEtl } from "./graph";

/** Une variable que l'on peut écrire dans un paramètre : `${NOM}`, `${sys:date}`, `${var:NOM}`… */
export interface VariableHint {
  token: string; // ce qui s'insère, sans `${}` : « NOM », « sys:date »…
  group: "param" | "flow" | "sys" | "global" | "var" | "env";
  value: string | null; // valeur connue dans l'éditeur, sinon null (connue seulement au run)
}

const pad = (n: number) => String(n).padStart(2, "0");

/** Valeurs de `${sys:…}` connues de l'éditeur ; le dossier du flux, l'utilisateur… ne le sont qu'au run. */
export function systemHints(now = new Date()): VariableHint[] {
  const date = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  const time = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
  return [
    ["date", date],
    ["datetime", `${date}T${time}`],
    ["timestamp", `${date.replaceAll("-", "")}_${time.replaceAll(":", "")}`],
    ["flow_dir", null],
    ["flow_name", null],
    ["flow_path", null],
    ["run_id", null],
    ["user", null],
    ["temp_dir", null],
  ].map(([name, value]) => ({ token: `sys:${name}`, group: "sys" as const, value }));
}

/** Variables posées par les SetVariables du flux (`${var:NOM}`). */
export function setterVariables(nodes: AppNode[]): string[] {
  const names: string[] = [];
  for (const node of nodes) {
    if (!isEtl(node) || node.data.type !== "SetVariables") continue;
    const specs = (node.data.params as { variables?: { name?: string }[] }).variables ?? [];
    for (const spec of specs) if (spec.name && !names.includes(spec.name)) names.push(spec.name);
  }
  return names;
}

/** Tout ce qui peut s'écrire entre `${` et `}` dans le flux ouvert. */
export function buildHints(meta: FlowMeta, nodes: AppNode[]): VariableHint[] {
  const known = new Map<string, string>(meta.publishedParams.map((p) => [p.name, p.default]));
  const hints: VariableHint[] = meta.publishedParams.map((p) => ({ token: p.name, group: "param", value: p.default }));
  const pending = meta.variables.filter((v) => v.name.trim());
  // les variables du flux peuvent citer les paramètres, les `sys:` et d'autres variables
  const sys = systemHints();
  const lookup = new Map<string, string | null>([...known, ...sys.map((h) => [h.token, h.value] as [string, string | null])]);
  for (let pass = 0; pass <= pending.length; pass++) {
    for (const v of pending) {
      if (lookup.has(v.name)) continue;
      const refs = [...v.value.matchAll(/\$\{([^}]+)\}/g)].map((m) => m[1]);
      if (refs.some((r) => pending.some((p) => p.name === r && !lookup.has(r)))) continue;
      lookup.set(v.name, resolvePreview(v.value, lookup).text);
    }
  }
  for (const v of pending) hints.push({ token: v.name, group: "flow", value: lookup.get(v.name) ?? null });
  hints.push(...sys);
  for (const name of setterVariables(nodes)) hints.push({ token: `var:${name}`, group: "var", value: null });
  return hints;
}

/** Valeur d'un texte avec ses `${…}` connus remplacés ; `complete` : plus rien d'inconnu dans le résultat. */
export function resolvePreview(
  text: string,
  known: Map<string, string | null> | VariableHint[],
): { text: string; complete: boolean } {
  const lookup = Array.isArray(known) ? new Map(known.map((h) => [h.token, h.value])) : known;
  let complete = true;
  const out = text.replace(/\$\{([^}]+)\}/g, (whole, key: string) => {
    const value = lookup.get(key);
    if (value === undefined || value === null) {
      complete = false;
      return whole;
    }
    return value;
  });
  return { text: out, complete };
}
