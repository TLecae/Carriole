import { describe, expect, it } from "vitest";
import type { FlowDoc } from "../api/types";
import { differsFrom, signatures, staleNodes } from "./staleness";

const node = (id: string, params: Record<string, unknown> = {}) => ({ id, type: "T", label: id, params, position: [0, 0] as [number, number], enabled: true });
const link = (from: string, to: string) => ({ id: `${from}-${to}`, from: [from, "output"] as [string, string], to: [to, "input"] as [string, string], enabled: true });

function flow(nodes: FlowDoc["nodes"], links: FlowDoc["links"]): FlowDoc {
  return { schema_version: 1, name: "f", description: "", published_params: [], nodes, links, annotations: [], bookmarks: [], subflows: {} };
}

// lecture → filtre → statistiques → écriture, et une autre branche lecture → tampon
const ran = flow(
  [node("read"), node("filter", { cond: "a > 1" }), node("stats", { by: ["dep"] }), node("write"), node("buffer", { d: 5 })],
  [link("read", "filter"), link("filter", "stats"), link("stats", "write"), link("read", "buffer")],
);
const before = signatures(ran);

describe("flux changé depuis un run (reprise d'une pause)", () => {
  it("le même flux, même déplacé : inchangé", () => {
    const moved = flow(ran.nodes.map((n) => ({ ...n, position: [50, 80] as [number, number] })), ran.links);
    expect(differsFrom(moved, before)).toBe(false);
  });
  it("nœud ajouté (même seul), retiré ou modifié : changé", () => {
    expect(differsFrom(flow([...ran.nodes, node("alone")], ran.links), before)).toBe(true);
    expect(differsFrom(flow(ran.nodes.filter((n) => n.id !== "write"), ran.links.filter((l) => l.to[0] !== "write")), before)).toBe(true);
    expect(differsFrom(flow(ran.nodes.map((n) => (n.id === "buffer" ? { ...n, params: { d: 9 } } : n)), ran.links), before)).toBe(true);
    expect(differsFrom(flow(ran.nodes, ran.links), undefined)).toBe(false);
  });
});

describe("nœuds à relancer après une modification", () => {
  it("rien n'a changé : aucun", () => {
    expect(staleNodes(ran, before).size).toBe(0);
  });

  it("un paramètre modifié : le nœud et tout son aval, pas l'autre branche", () => {
    const now = { ...ran, nodes: ran.nodes.map((n) => (n.id === "filter" ? { ...n, params: { cond: "a > 2" } } : n)) };
    expect([...staleNodes(now, before)].sort()).toEqual(["filter", "stats", "write"]);
  });

  it("renommer un nœud ou réordonner les clés de ses paramètres ne change rien", () => {
    const now = { ...ran, nodes: ran.nodes.map((n) => (n.id === "stats" ? { ...n, label: "Mes stats", params: { by: ["dep"] } } : n)) };
    expect(staleNodes(now, before).size).toBe(0);
  });

  it("désactiver un nœud ou une liaison, ou insérer un nœud, invalide l'aval", () => {
    const off = { ...ran, nodes: ran.nodes.map((n) => (n.id === "read" ? { ...n, enabled: false } : n)) };
    expect([...staleNodes(off, before)].sort()).toEqual(["buffer", "filter", "read", "stats", "write"]);
    const inserted = flow([...ran.nodes, node("sort")], [link("read", "filter"), link("filter", "sort"), link("sort", "stats"), link("stats", "write"), link("read", "buffer")]);
    expect([...staleNodes(inserted, before)].sort()).toEqual(["stats", "write"]);
  });

  it("un paramètre publié modifié : seuls les nœuds qui s'en servent, et leur aval", () => {
    const withParam = flow(
      [node("read", { path: "${DOSSIER}/a.csv" }), node("filter"), node("buffer", { d: 5 })],
      [link("read", "filter"), link("read", "buffer")],
    );
    const sig = signatures(withParam);
    const changed = { ...withParam, variables: { DOSSIER: "D:/autre" } };
    expect([...staleNodes(changed, sig)].sort()).toEqual(["buffer", "filter", "read"]);
    const other = flow([node("read"), node("filter")], [link("read", "filter")]);
    expect(staleNodes({ ...other, variables: { X: "1" } }, signatures(other)).size).toBe(0);
  });

  it("sans run : aucun", () => {
    expect(staleNodes(ran, undefined).size).toBe(0);
  });
});
