import type { PortRef } from "../api/types";
import type { RunState } from "./runState";

type RunView = Pick<RunState, "id" | "nodes">;

/** Un nouveau run : le port suivi passe dans ce run une fois qu'il y a ses données (pas avant : « données
 * introuvables » resterait affiché). Pas si un autre nœud est sélectionné : un nœud de l'amont, repris du cache dès
 * le début du run, ne prend pas la place du nœud choisi. */
export function followsRun(target: PortRef | null, run: RunView, selectedId: string | undefined): boolean {
  if (!run.id || !target || target.run === run.id) return false;
  if (run.nodes[target.node]?.rows?.[target.port] === undefined) return false;
  return !selectedId || selectedId === target.node;
}

/** Le nœud affiché (choisi sans données, ou celui du port suivi, d'un run précédent) tourne dans ce run : on montre
 * son calcul, ni ses anciennes données ni « lancez le flux ». */
export function computingNode(run: RunView, emptyNode: string | null, target: PortRef | null): string | null {
  const computing = (node: string) => ["pending", "running"].includes(run.nodes[node]?.status ?? "");
  if (emptyNode) return computing(emptyNode) ? emptyNode : null;
  return target && target.run !== run.id && computing(target.node) ? target.node : null;
}
