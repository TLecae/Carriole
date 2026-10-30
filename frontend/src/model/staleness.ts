import type { FlowDoc } from "../api/types";

/** Texte stable d'une valeur (clés d'objet triées) : deux réglages égaux donnent le même texte. */
function stable(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`;
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
    return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${stable(v)}`).join(",")}}`;
  }
  return JSON.stringify(value ?? null);
}

/** Ce qui décide du résultat de chaque nœud : son type, ses paramètres, son activation, sa version de composant et
 * ses liaisons entrantes (le nom affiché n'en fait pas partie). Un nœud qui se sert de `${…}` dépend aussi des
 * paramètres publiés et des variables du flux. */
export function signatures(doc: FlowDoc): Record<string, string> {
  const inbound: Record<string, string[]> = {};
  for (const link of doc.links) {
    if (link.enabled === false) continue;
    (inbound[link.to[0]] ??= []).push(`${link.from[0]}.${link.from[1]}>${link.to[1]}`);
  }
  const flowValues = stable({ published: doc.published_params, variables: doc.variables ?? {} });
  const out: Record<string, string> = {};
  for (const node of doc.nodes) {
    const params = stable(node.params);
    out[node.id] = stable({
      type: node.type,
      params,
      enabled: node.enabled !== false,
      version: node.component_version ?? null,
      inbound: (inbound[node.id] ?? []).sort(),
      flow: params.includes("${") ? flowValues : null,
    });
  }
  return out;
}

/** Nœuds dont le résultat affiché (celui du run) ne correspond plus au flux : ceux qui ont changé depuis le run, et
 * tous ceux qui en dépendent en aval. Un nœud ajouté depuis n'a pas de résultat : seul son aval est concerné. */
export function staleNodes(current: FlowDoc, ran: Record<string, string> | undefined): Set<string> {
  const stale = new Set<string>();
  if (!ran) return stale;
  const now = signatures(current);
  const downstream: Record<string, string[]> = {};
  for (const link of current.links) {
    if (link.enabled !== false) (downstream[link.from[0]] ??= []).push(link.to[0]);
  }
  const todo = Object.keys(now).filter((id) => id in ran && ran[id] !== now[id]);
  for (const id of Object.keys(now)) {
    if (!(id in ran)) todo.push(...(downstream[id] ?? [])); // nœud ajouté : son aval change d'entrée
  }
  while (todo.length) {
    const id = todo.pop()!;
    if (stale.has(id)) continue;
    stale.add(id);
    todo.push(...(downstream[id] ?? []));
  }
  return stale;
}
