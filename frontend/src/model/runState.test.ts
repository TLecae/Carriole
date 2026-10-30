import { describe, expect, it } from "vitest";
import type { NodeResult, RunDetail } from "../api/types";
import { applyResult, IDLE_RUN, reduceEvent } from "./runState";

const result = (nodes: Record<string, Partial<NodeResult>>): RunDetail["result"] => ({ status: "done", seconds: 1, error: null, nodes: nodes as Record<string, NodeResult> });

describe("reduceEvent", () => {
  it("run_started garde les nœuds repris annoncés avant lui (run « jusqu'au nœud »)", () => {
    let run = reduceEvent(IDLE_RUN, { type: "node", node: "autre", status: "cached", rows: { output: 3 } });
    run = reduceEvent(run, { type: "run_started", nodes: ["cible"] });
    expect(run.nodes.autre).toMatchObject({ status: "cached", rows: { output: 3 } });
    expect(run.nodes.cible.status).toBe("pending");
  });
});

describe("applyResult", () => {
  it("le résultat enregistré termine un nœud resté « en cours » faute de son dernier événement", () => {
    let run = reduceEvent(IDLE_RUN, { type: "run_started", nodes: ["r", "w"] });
    run = reduceEvent(run, { type: "node", node: "w", status: "running" });
    run = applyResult(run, result({ r: { status: "done", rows: { output: 5 } }, w: { status: "done", rows: {}, rows_in: { input: 5 } } }));
    expect(run.nodes.w.status).toBe("done");
    expect(run.nodes.r.rows).toEqual({ output: 5 });
    expect(run.counts["r/output"]).toBe(5);
  });

  it("« pas exécuté » n'efface pas un nœud connu, mais remplace un « en attente » sans suite", () => {
    let run = reduceEvent(IDLE_RUN, { type: "node", node: "repris", status: "cached", rows: { output: 2 } });
    run = reduceEvent(run, { type: "run_started", nodes: ["a"] });
    run = applyResult(run, result({ repris: { status: "not_run" }, a: { status: "not_run" }, b: { status: "not_run" } }));
    expect(run.nodes.repris.status).toBe("cached");
    expect(run.nodes.a.status).toBe("not_run");
    expect(run.nodes.b).toBeUndefined();
  });

  it("sans résultat (run en cours), rien ne change", () => {
    expect(applyResult(IDLE_RUN, null)).toBe(IDLE_RUN);
  });
});
