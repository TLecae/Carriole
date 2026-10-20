import { describe, expect, it } from "vitest";
import type { FlowDoc } from "../api/types";
import { applyCollapsed, fromFlowDoc, layoutBookmarks, nextId, toFlowDoc, upstreamAttributes } from "./graph";

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
});
