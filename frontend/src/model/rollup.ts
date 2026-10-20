import type { NodeResult } from "../api/types";

type NodeStatus = NonNullable<NodeResult["status"]>;

const SEP = "__";
const ROLLED = "rolled"; // marque les entrées fabriquées ici : elles sont recalculées à chaque appel, jamais prises pour des nœuds réels

/**
 * Un nœud « composant » est remplacé par son contenu à l'exécution : ses nœuds internes s'appellent
 * `<composant>__<interne>`. Ce résumé donne au nœud du canvas l'état de l'ensemble de ses nœuds internes.
 */
export function rollupComponents<T extends Partial<NodeResult>>(nodes: Record<string, T>): Record<string, T> {
  const groups = new Map<string, T[]>();
  for (const [id, result] of Object.entries(nodes)) {
    const cut = id.indexOf(SEP);
    if (cut <= 0) continue;
    const outer = id.slice(0, cut);
    if (outer in nodes && !(nodes[outer] as Record<string, unknown>)[ROLLED]) continue; // vrai nœud du même nom
    groups.set(outer, [...(groups.get(outer) ?? []), result]);
  }
  if (groups.size === 0) return nodes;
  const out = { ...nodes };
  for (const [outer, parts] of groups) {
    const has = (s: NodeStatus) => parts.some((p) => p.status === s);
    const status: NodeStatus = has("error")
      ? "error"
      : has("cancelled")
        ? "cancelled"
        : has("running")
          ? "running"
          : has("pending")
            ? "pending"
            : parts.every((p) => p.status === "cached")
              ? "cached"
              : parts.every((p) => p.status === "disabled")
                ? "disabled"
                : parts.every((p) => p.status === "not_run")
                  ? "not_run"
                  : "done";
    const seconds = parts.reduce((total, p) => total + (p.seconds ?? 0), 0);
    out[outer] = {
      status,
      seconds: status === "running" || status === "pending" ? undefined : Math.round(seconds * 1000) / 1000,
      error: parts.find((p) => p.error)?.error ?? null,
      [ROLLED]: true,
    } as unknown as T;
  }
  return out;
}
