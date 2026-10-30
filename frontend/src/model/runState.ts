import type { NodeResult, RunDetail, RunEvent, RunStatus } from "../api/types";
import { rollupComponents } from "./rollup";

export interface RunState {
  id: string | null;
  status: RunStatus | "idle";
  error: string | null;
  nodes: Record<string, Partial<NodeResult>>;
  counts: Record<string, number>;
  logs: RunEvent[];
  cache: boolean;
  signatures?: Record<string, string>; // nœuds du flux exécuté (`staleness.signatures`)
}

export const IDLE_RUN: RunState = { id: null, status: "idle", error: null, nodes: {}, counts: {}, logs: [], cache: true };

export function reduceEvent(run: RunState, e: RunEvent): RunState {
  const next = reduceRaw(run, e);
  return e.type === "node" || e.type === "run_started" ? { ...next, nodes: rollupComponents(next.nodes) } : next;
}

function reduceRaw(run: RunState, e: RunEvent): RunState {
  switch (e.type) {
    case "run_started":
      // les nœuds du run passent « en attente » ; les autres gardent leur état (repris du run précédent)
      return { ...run, status: "running", nodes: { ...run.nodes, ...Object.fromEntries((e.nodes ?? []).map((id) => [id, { status: "pending" as const }])) } };
    case "node":
      return {
        ...run,
        nodes: {
          ...run.nodes,
          [e.node!]: {
            ...run.nodes[e.node!],
            status: e.status as NodeResult["status"],
            seconds: e.seconds,
            rows: e.rows,
            rows_in: e.rows_in,
            rss_mb: e.rss_mb ?? null,
            error: e.error ?? null,
          },
        },
      };
    case "counts":
      return { ...run, counts: { ...run.counts, [`${e.node}/${e.port}`]: e.count ?? 0 } };
    case "log":
      return { ...run, logs: [...run.logs, e].slice(-5000) };
    case "run_finished":
      return { ...run, status: (e.status as RunStatus) ?? "done", error: e.error ?? null };
    default:
      return run;
  }
}

/** Le résultat enregistré d'un run fini fait foi : l'état de chaque nœud, ses comptes et son erreur, même si des
 * événements manquent (flux coupé, run relu longtemps après). Un nœud « pas exécuté » n'efface pas un état connu,
 * sauf un « en attente » ou « en cours » resté sans suite. */
export function applyResult(run: RunState, result: RunDetail["result"]): RunState {
  if (!result) return run;
  const nodes = { ...run.nodes };
  const counts = { ...run.counts };
  for (const [id, r] of Object.entries(result.nodes)) {
    const known = nodes[id]?.status;
    if ((r.status === "not_run" || r.status === "disabled") && known !== "pending" && known !== "running") continue;
    nodes[id] = { ...nodes[id], status: r.status, seconds: r.seconds, rows: r.rows, rows_in: r.rows_in, rss_mb: r.rss_mb ?? null, error: r.error ?? null };
    for (const [port, n] of Object.entries(r.rows ?? {})) counts[`${id}/${port}`] = n;
  }
  return { ...run, nodes: rollupComponents(nodes), counts };
}
