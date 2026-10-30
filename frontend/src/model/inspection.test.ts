import { describe, expect, it } from "vitest";
import { computingNode, followsRun } from "./inspection";

const reader = { run: "r1", node: "reader", port: "output" };

describe("inspecteur pendant un run", () => {
  // run r2 lancé jusqu'à « stats » : le lecteur est repris du cache d'emblée, « stats » calcule encore
  const run = { id: "r2", nodes: { reader: { status: "cached" as const, rows: { output: 20 } }, stats: { status: "running" as const } } };

  it("le nœud choisi garde sa place : un nœud de l'amont repris du cache ne le remplace pas", () => {
    expect(followsRun(reader, run, "stats")).toBe(false);
    expect(followsRun(reader, run, "reader")).toBe(true);
    expect(followsRun(reader, run, undefined)).toBe(true); // rien de sélectionné : on suit le port
  });

  it("le port suivi ne passe dans le run qu'une fois ses données là", () => {
    expect(followsRun({ run: "r1", node: "stats", port: "summary" }, run, "stats")).toBe(false);
    expect(followsRun({ ...reader, run: "r2" }, run, "reader")).toBe(false); // déjà dans ce run
  });

  it("un nœud qui calcule l'annonce, au lieu de ses anciennes données ou de « lancez le flux »", () => {
    expect(computingNode(run, "stats", reader)).toBe("stats");
    expect(computingNode(run, null, { run: "r1", node: "stats", port: "summary" })).toBe("stats");
    expect(computingNode({ ...run, nodes: { ...run.nodes, stats: { status: "pending" } } }, "stats", null)).toBe("stats");
    expect(computingNode(run, null, reader)).toBe(null); // repris du cache : ses données sont là
    expect(computingNode({ ...run, nodes: { stats: { status: "done" } } }, "stats", null)).toBe(null);
  });
});
