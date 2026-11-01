import { describe, expect, it } from "vitest";
import type { FlowDoc } from "../api/types";
import { applyCollapsed, fromFlowDoc, inputFor, layoutBookmarks, nextId, spliceEdge, toFlowDoc, upstreamAttributes, type AppEdge } from "./graph";

const doc: FlowDoc = {
  schema_version: 1,
  name: "demo",
  description: "d",
  component: null,
  published_params: [{ name: "DEP", label: "", type: "text", default: "31", required: false, choices: [] }],
  nodes: [
    { id: "Reader_1", type: "Reader", label: "Lecture", params: { path: "/data/in.parquet" }, position: [0, 0], enabled: true },
    { id: "ExtractByExpression_1", type: "ExtractByExpression", label: "", params: { conditions: ['"a" > 1'] }, position: [300, 40], enabled: false },
  ],
  links: [{ id: "l1", from: ["Reader_1", "output"], to: ["ExtractByExpression_1", "input"], enabled: true }],
  annotations: [{ id: "note_1", text: "à vérifier", position: [10, 200] }],
  bookmarks: [{ id: "bm_1", name: "Entrée", color: "#cfe3ff", nodes: ["Reader_1", "ExtractByExpression_1"], collapsed: false }],
  subflows: {},
  variables: {},
};

describe("conversion flux <-> graphe", () => {
  it("fait l'aller-retour sans rien perdre", () => {
    const { nodes, edges, meta } = fromFlowDoc(doc);
    expect(toFlowDoc(meta, nodes, edges)).toEqual(doc);
  });

  it("conserve la version épinglée d'un nœud composant (et rien pour les autres)", () => {
    const withComponent: FlowDoc = {
      ...doc,
      nodes: [
        ...doc.nodes,
        { id: "f", type: "component:filtre", label: "", params: {}, position: [0, 0], enabled: true, component_version: 2 },
        { id: "g", type: "component:filtre", label: "", params: {}, position: [0, 0], enabled: true, component_version: null },
      ],
    };
    const { nodes, edges, meta } = fromFlowDoc(withComponent);
    expect(toFlowDoc(meta, nodes, edges)).toEqual(withComponent);
  });

  it("calcule la zone d'un signet autour de ses nœuds", () => {
    const { nodes } = fromFlowDoc(doc);
    const bm = layoutBookmarks(nodes).find((n) => n.type === "bookmark")!;
    expect(bm.position.x).toBeLessThan(0);
    expect((bm.width ?? 0) > 300).toBe(true);
  });

  it("cache les nœuds d'un signet replié", () => {
    const { nodes } = fromFlowDoc({ ...doc, bookmarks: [{ ...doc.bookmarks[0], collapsed: true }] });
    const out = applyCollapsed(nodes);
    expect(out.find((n) => n.id === "Reader_1")?.hidden).toBe(true);
    expect(out.find((n) => n.id === "note_1")?.hidden).toBeFalsy();
  });

  it("garde une annotation liée : relative à son nœud dans l'éditeur, absolue dans le fichier", () => {
    const linked: FlowDoc = { ...doc, annotations: [{ id: "note_1", text: "à vérifier", position: [310, 0], node: "ExtractByExpression_1" }] };
    const { nodes, edges, meta } = fromFlowDoc(linked);
    const note = nodes.find((n) => n.id === "note_1")!;
    expect(note.parentId).toBe("ExtractByExpression_1");
    expect(note.position).toEqual({ x: 10, y: -40 });
    expect(nodes.indexOf(note)).toBeGreaterThan(nodes.findIndex((n) => n.id === "ExtractByExpression_1"));
    expect(toFlowDoc(meta, nodes, edges)).toEqual(linked);
    // le nœud déplacé emmène l'annotation
    const moved = nodes.map((n) => (n.id === "ExtractByExpression_1" ? { ...n, position: { x: 500, y: 100 } } : n));
    expect(toFlowDoc(meta, moved, edges).annotations[0].position).toEqual([510, 60]);
  });

  it("cache l'annotation liée à un nœud replié", () => {
    const { nodes } = fromFlowDoc({
      ...doc,
      annotations: [{ id: "note_1", text: "", position: [0, 0], node: "Reader_1" }],
      bookmarks: [{ ...doc.bookmarks[0], collapsed: true }],
    });
    expect(applyCollapsed(nodes).find((n) => n.id === "note_1")?.hidden).toBe(true);
  });
});

describe("liaisons", () => {
  const link = (id: string, from: string, fromPort: string, to: string, toPort: string, enabled = true): AppEdge => ({
    id,
    source: from,
    sourceHandle: fromPort,
    target: to,
    targetHandle: toPort,
    type: "etl",
    data: { enabled },
  });

  it("branche sur la première entrée libre, sinon la première", () => {
    const edges = [link("l_1", "A", "output", "J", "base")];
    expect(inputFor("J", ["base", "related"], edges)).toBe("related");
    expect(inputFor("J", ["base"], edges)).toBe("base");
    expect(inputFor("J", [], edges)).toBeNull();
  });

  it("intercale un nœud dans une liaison, à sa place et avec son état", () => {
    const edges = [link("l_1", "A", "Passed", "C", "input", false), link("l_2", "C", "output", "D", "input")];
    const out = spliceEdge(edges, "l_1", "B", { inputs: ["input"], outputs: ["output", "Rejected"] })!;
    expect(out.map((e) => [e.id, e.source, e.sourceHandle, e.target, e.targetHandle, e.data?.enabled])).toEqual([
      ["l_3", "A", "Passed", "B", "input", false],
      ["l_4", "B", "output", "C", "input", false],
      ["l_2", "C", "output", "D", "input", true],
    ]);
  });

  it("refuse un nœud sans entrée ou sans sortie, ou déjà au bout de la liaison", () => {
    const edges = [link("l_1", "A", "output", "C", "input")];
    expect(spliceEdge(edges, "l_1", "W", { inputs: ["input"], outputs: [] })).toBeNull();
    expect(spliceEdge(edges, "l_1", "C", { inputs: ["input"], outputs: ["output"] })).toBeNull();
    expect(spliceEdge(edges, "inconnue", "B", { inputs: ["input"], outputs: ["output"] })).toBeNull();
  });
});

describe("identifiants", () => {
  it("prend le premier numéro libre", () => {
    expect(nextId("ExtractByExpression", ["ExtractByExpression_1", "ExtractByExpression_3"])).toBe("ExtractByExpression_2");
    expect(nextId("Reader", [])).toBe("Reader_1");
  });
});

describe("attributs disponibles en amont", () => {
  it("réunit les attributs des liens actifs, sans doublon", () => {
    const { edges } = fromFlowDoc(doc);
    const schemas = { "Reader_1/output": { fields: [{ name: "id" }, { name: "nom" }] } };
    expect(upstreamAttributes("ExtractByExpression_1", edges, schemas)).toEqual(["id", "nom"]);
    expect(upstreamAttributes("Reader_1", edges, schemas)).toEqual([]);
    expect(upstreamAttributes("ExtractByExpression_1", [{ ...edges[0], data: { enabled: false } }], schemas)).toEqual([]);
  });

  it("ceux d'une entrée seulement (relateur spatial : les attributs de « related »)", () => {
    const edges = [
      { id: "a", source: "Parcelles", sourceHandle: "output", target: "J", targetHandle: "base" },
      { id: "b", source: "Communes", sourceHandle: "output", target: "J", targetHandle: "related" },
    ];
    const schemas = { "Parcelles/output": { fields: [{ name: "id" }] }, "Communes/output": { fields: [{ name: "nom" }, { name: "pop" }] } };
    expect(upstreamAttributes("J", edges, schemas)).toEqual(["id", "nom", "pop"]);
    expect(upstreamAttributes("J", edges, schemas, "related")).toEqual(["nom", "pop"]);
    expect(upstreamAttributes("J", edges, schemas, "base")).toEqual(["id"]);
  });
});
