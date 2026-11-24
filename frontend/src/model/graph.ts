import type { Edge, Node } from "@xyflow/react";
import type { AnnotationDoc, BookmarkDoc, ComponentMeta, FlowDoc, JsonSchema, PublishedParam, ModifierInfo } from "../api/types";

// ---- types des nœuds et des liens de l'éditeur ---------------------------------------

export type EtlData = {
  kind: "etl";
  type: string; // nom du modificateur
  label: string;
  params: Record<string, unknown>;
  enabled: boolean;
  componentVersion?: number | null; // nœud composant : version épinglée (null : la dernière publiée)
} & Record<string, unknown>;

export type NoteData = { kind: "note"; text: string } & Record<string, unknown>;

export type BookmarkData = {
  kind: "bookmark";
  name: string;
  color: string;
  members: string[];
  collapsed: boolean;
} & Record<string, unknown>;

export type EtlNode = Node<EtlData, "etl">;
export type NoteNode = Node<NoteData, "note">;
export type BookmarkNode = Node<BookmarkData, "bookmark">;
export type AppNode = EtlNode | NoteNode | BookmarkNode;
export type EdgeData = { enabled: boolean } & Record<string, unknown>;
export type AppEdge = Edge<EdgeData>;

export const isEtl = (n: AppNode): n is EtlNode => n.type === "etl";
export const isNote = (n: AppNode): n is NoteNode => n.type === "note";
export const isBookmark = (n: AppNode): n is BookmarkNode => n.type === "bookmark";

export interface FlowMeta {
  name: string;
  description: string;
  publishedParams: PublishedParam[];
  variables: { name: string; value: string }[]; // variables du flux : constantes ou composées (${AUTRE})
  component: ComponentMeta | null; // non nul : ce flux est un composant réutilisable
}

// ---- identifiants ------------------------------------------------------------------------

/** Préfixe d'identifiant d'un nœud : le nom du modificateur ; pour un composant (`component:nom`), le nom sans le préfixe. */
export function idPrefix(type: string): string {
  return type.replace(/^component:/, "").replace(/[^A-Za-z0-9_.-]+/g, "_");
}

export function nextId(prefix: string, taken: Iterable<string>): string {
  const used = new Set(taken);
  let i = 1;
  while (used.has(`${prefix}_${i}`)) i += 1;
  return `${prefix}_${i}`;
}

// ---- liaisons ------------------------------------------------------------------------------

/** Port d'entrée où brancher une nouvelle liaison vers `node` : le premier libre, sinon le premier. */
export function inputFor(node: string, inputs: string[], edges: AppEdge[]): string | null {
  const used = new Set(edges.filter((e) => e.target === node).map((e) => e.targetHandle ?? "input"));
  return inputs.find((p) => !used.has(p)) ?? inputs[0] ?? null;
}

/** Intercale `node` dans la liaison `edgeId` : source → première entrée de `node`, première sortie → cible.
 * Null si c'est impossible (nœud sans entrée ou sans sortie, liaison inconnue ou déjà reliée à `node`). */
export function spliceEdge(edges: AppEdge[], edgeId: string, node: string, ports: { inputs: string[]; outputs: string[] }): AppEdge[] | null {
  const edge = edges.find((e) => e.id === edgeId);
  if (!edge || edge.source === node || edge.target === node || !ports.inputs.length || !ports.outputs.length) return null;
  const first = nextId("l", edges.map((e) => e.id));
  const second = nextId("l", [...edges.map((e) => e.id), first]);
  const data = { enabled: edge.data?.enabled ?? true };
  const into: AppEdge = { id: first, source: edge.source, sourceHandle: edge.sourceHandle ?? "output", target: node, targetHandle: ports.inputs[0], type: "etl", data };
  const out: AppEdge = { id: second, source: node, sourceHandle: ports.outputs[0], target: edge.target, targetHandle: edge.targetHandle ?? "input", type: "etl", data };
  return edges.flatMap((e) => (e.id === edgeId ? [into, out] : [e])); // à la place de l'ancienne : l'ordre des liaisons est gardé
}

// ---- document de flux <-> graphe de l'éditeur ----------------------------------------------

export function toFlowDoc(meta: FlowMeta, nodes: AppNode[], edges: AppEdge[]): FlowDoc {
  const annotations: AnnotationDoc[] = [];
  const bookmarks: BookmarkDoc[] = [];
  const docNodes: FlowDoc["nodes"] = [];
  const byId = new Map(nodes.map((n) => [n.id, n]));
  for (const n of nodes) {
    if (isEtl(n)) {
      docNodes.push({
        id: n.id,
        type: n.data.type,
        label: n.data.label,
        params: n.data.params,
        position: [round(n.position.x), round(n.position.y)],
        enabled: n.data.enabled,
        ...(n.data.type.startsWith("component:") ? { component_version: n.data.componentVersion ?? null } : {}),
      });
    } else if (isNote(n)) {
      // annotation liée : position relative au nœud dans l'éditeur, absolue dans le fichier
      const parent = n.parentId ? byId.get(n.parentId) : undefined;
      const x = n.position.x + (parent?.position.x ?? 0);
      const y = n.position.y + (parent?.position.y ?? 0);
      annotations.push({ id: n.id, text: n.data.text, position: [round(x), round(y)], ...(parent ? { node: parent.id } : {}) });
    } else {
      bookmarks.push({
        id: n.id,
        name: n.data.name,
        color: n.data.color,
        nodes: n.data.members,
        collapsed: n.data.collapsed,
      });
    }
  }
  return {
    schema_version: 1,
    name: meta.name,
    description: meta.description,
    published_params: meta.publishedParams,
    variables: Object.fromEntries(meta.variables.filter((v) => v.name.trim()).map((v) => [v.name.trim(), v.value])),
    component: meta.component,
    nodes: docNodes,
    links: edges.map((e) => ({
      id: e.id,
      from: [e.source, e.sourceHandle ?? "output"],
      to: [e.target, e.targetHandle ?? "input"],
      enabled: e.data?.enabled ?? true,
    })),
    annotations,
    bookmarks,
    subflows: {},
  };
}

export function fromFlowDoc(doc: FlowDoc): { nodes: AppNode[]; edges: AppEdge[]; meta: FlowMeta } {
  const nodes: AppNode[] = [];
  for (const b of doc.bookmarks) {
    nodes.push({
      id: b.id,
      type: "bookmark",
      position: { x: 0, y: 0 },
      zIndex: -1,
      data: { kind: "bookmark", name: b.name, color: b.color, members: b.nodes, collapsed: b.collapsed },
    });
  }
  for (const n of doc.nodes) {
    nodes.push({
      id: n.id,
      type: "etl",
      position: { x: n.position[0], y: n.position[1] },
      data: { kind: "etl", type: n.type, label: n.label, params: n.params, enabled: n.enabled, componentVersion: n.component_version ?? null },
    });
  }
  const docNodes = new Map(doc.nodes.map((n) => [n.id, n]));
  for (const a of doc.annotations) {
    const parent = a.node ? docNodes.get(a.node) : undefined;
    const position = parent
      ? { x: a.position[0] - parent.position[0], y: a.position[1] - parent.position[1] }
      : { x: a.position[0], y: a.position[1] };
    // après les nœuds : React Flow veut le parent avant l'annotation qui le suit
    nodes.push({ id: a.id, type: "note", position, ...(parent ? { parentId: parent.id } : {}), data: { kind: "note", text: a.text } });
  }
  const edges: AppEdge[] = doc.links.map((l) => ({
    id: l.id,
    source: l.from[0],
    sourceHandle: l.from[1],
    target: l.to[0],
    targetHandle: l.to[1],
    type: "etl",
    data: { enabled: l.enabled },
  }));
  return {
    nodes: layoutBookmarks(nodes),
    edges,
    meta: {
      name: doc.name,
      description: doc.description,
      publishedParams: doc.published_params,
      variables: Object.entries(doc.variables ?? {}).map(([name, value]) => ({ name, value })),
      component: doc.component ?? null,
    },
  };
}

const round = (v: number) => Math.round(v * 10) / 10;

// ---- signets : zone colorée calculée d'après les nœuds qu'elle contient -------------------

export const NODE_WIDTH = 190;
const PORT_ROW = 22;
const HEADER = 34;

export function nodeSize(n: AppNode, info?: ModifierInfo): { width: number; height: number } {
  if (isEtl(n)) {
    const ports = Math.max(info?.inputs.length ?? 1, info?.outputs.length ?? 1);
    return { width: n.measured?.width ?? NODE_WIDTH, height: n.measured?.height ?? HEADER + ports * PORT_ROW + 12 };
  }
  return { width: n.measured?.width ?? 160, height: n.measured?.height ?? 70 };
}

export function layoutBookmarks(nodes: AppNode[], palette?: Map<string, ModifierInfo>): AppNode[] {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  return nodes.map((n) => {
    if (!isBookmark(n)) return n;
    const members = n.data.members.map((id) => byId.get(id)).filter((m): m is AppNode => !!m && !isBookmark(m));
    if (members.length === 0) return { ...n, draggable: false, width: 220, height: 60 };
    const pad = 28;
    let x0 = Infinity;
    let y0 = Infinity;
    let x1 = -Infinity;
    let y1 = -Infinity;
    for (const m of members) {
      const info = isEtl(m) ? palette?.get(m.data.type) : undefined;
      const { width, height } = nodeSize(m, info);
      x0 = Math.min(x0, m.position.x);
      y0 = Math.min(y0, m.position.y);
      x1 = Math.max(x1, m.position.x + width);
      y1 = Math.max(y1, m.position.y + height);
    }
    if (n.data.collapsed) {
      return { ...n, draggable: false, position: { x: x0 - pad, y: y0 - pad - 26 }, width: Math.max(220, (x1 - x0) / 2), height: 52 };
    }
    return {
      ...n,
      draggable: false,
      position: { x: x0 - pad, y: y0 - pad - 26 },
      width: x1 - x0 + 2 * pad,
      height: y1 - y0 + 2 * pad + 26,
    };
  });
}

/** Applique l'état « replié » : les nœuds d'un signet replié sont cachés. */
export function applyCollapsed(nodes: AppNode[]): AppNode[] {
  const hidden = new Set<string>();
  for (const n of nodes) if (isBookmark(n) && n.data.collapsed) n.data.members.forEach((id) => hidden.add(id));
  for (const n of nodes) if (n.parentId && hidden.has(n.parentId)) hidden.add(n.id); // annotation liée à un nœud replié
  return nodes.map((n) => (isBookmark(n) ? n : !!n.hidden === hidden.has(n.id) ? n : { ...n, hidden: hidden.has(n.id) }));
}

// ---- ports nommés par les paramètres ---------------------------------------------------------

const portNames = (value: unknown, key?: string): string[] =>
  Array.isArray(value) ? value.map((v) => String((key ? (v as Record<string, unknown> | null)?.[key] : v) ?? "")) : [];

/**
 * Renommages d'une liste de noms éditée ligne à ligne (ports, paramètres publiés, variables) : un nom changé à la
 * même place, sauf échange de deux noms ; vidé puis ressaisi aussi (`held` garde l'ancien nom, clé `slot/ligne`).
 * Une ligne ajoutée ou retirée décale les autres : rien n'est renommé.
 */
export function renamesByIndex(old: string[], now: string[], held: Map<string, string>, slot: string): Map<string, string> {
  const out = new Map<string, string>();
  if (old.length !== now.length) {
    for (const k of [...held.keys()]) if (k.startsWith(`${slot}/`)) held.delete(k);
    return out;
  }
  old.forEach((name, i) => {
    const key = `${slot}/${i}`;
    const next = now[i];
    if (name === next) return;
    if (!next) {
      if (name && !now.includes(name)) held.set(key, name); // vidé : ses références attendent le nouveau nom
      return;
    }
    const from = name || held.get(key);
    held.delete(key);
    if (from && !now.includes(from) && !old.includes(next)) out.set(from, next);
  });
  return out;
}

const REFERENCE = /\$\{([^}]+)\}/g;

/** `${ancien}` devient `${nouveau}` dans toutes les valeurs texte de `value` (paramètres d'un nœud, variables). */
export function renameReferences<T>(value: T, renames: Map<string, string>): T {
  const walk = (v: unknown): unknown => {
    if (typeof v === "string") return v.replace(REFERENCE, (whole, key: string) => (renames.has(key) ? `\${${renames.get(key)}}` : whole));
    if (Array.isArray(v)) return v.map(walk);
    if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, walk(x)]));
    return v;
  };
  return renames.size ? (walk(value) as T) : value;
}

/** `value` contient une référence `${nom}` à l'un des noms renommés. */
export const refersTo = (value: unknown, renames: Map<string, string>): boolean => {
  const text = JSON.stringify(value) ?? "";
  return [...renames.keys()].some((name) => text.includes(`\${${name}}`));
};

/**
 * Liaisons d'un nœud dont une liste de paramètres nomme les ports (`x-etl-ports` : sorties de RouteByExpression,
 * entrées d'ExecuteSql…), après un changement de ses paramètres : un port renommé garde ses liaisons, y compris vidé
 * puis ressaisi (`held` garde son ancien nom d'une frappe à l'autre) ; une ligne supprimée retire les liaisons de son
 * port. Les liaisons inchangées gardent leur identité (même tableau si rien ne change).
 */
export function followPortRenames(
  edges: AppEdge[],
  nodeId: string,
  schema: JsonSchema | undefined,
  before: Record<string, unknown>,
  after: Record<string, unknown>,
  held: Map<string, string>,
): AppEdge[] {
  const renamed = new Map<string, string>(); // « in/ancien » -> nouveau
  const dropped = new Set<string>(); // « in/nom »
  for (const [field, prop] of Object.entries(schema?.properties ?? {})) {
    const spec = prop["x-etl-ports"];
    if (!spec || before[field] === after[field]) continue;
    const old = portNames(before[field], spec.key);
    const now = portNames(after[field], spec.key);
    for (const [from, to] of renamesByIndex(old, now, held, `${nodeId}/${field}`)) renamed.set(`${spec.side}/${from}`, to);
    if (now.length < old.length) for (const name of old) if (name && !now.includes(name)) dropped.add(`${spec.side}/${name}`);
  }
  if (!renamed.size && !dropped.size) return edges;
  let changed = false;
  const out: AppEdge[] = [];
  for (const e of edges) {
    const side = e.source === nodeId ? "out" : e.target === nodeId ? "in" : null;
    const port = side === "out" ? (e.sourceHandle ?? "output") : side === "in" ? (e.targetHandle ?? "input") : null;
    const key = side && `${side}/${port}`;
    if (key && dropped.has(key)) {
      changed = true;
      continue;
    }
    const next = key ? renamed.get(key) : undefined;
    if (next) {
      changed = true;
      out.push(side === "out" ? { ...e, sourceHandle: next } : { ...e, targetHandle: next });
    } else out.push(e);
  }
  return changed ? out : edges;
}

// ---- schéma disponible en amont d'un nœud ---------------------------------------------------

export function upstreamAttributes(
  nodeId: string,
  edges: AppEdge[],
  schemas: Record<string, { fields: { name: string }[] } | null> | undefined,
  port?: string, // une entrée seulement (« related », « join »…), sinon toutes
): string[] {
  if (!schemas) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const e of edges) {
    if (e.target !== nodeId || e.data?.enabled === false) continue;
    if (port !== undefined && (e.targetHandle ?? "input") !== port) continue;
    const schema = schemas[`${e.source}/${e.sourceHandle ?? "output"}`];
    for (const f of schema?.fields ?? []) {
      if (!seen.has(f.name)) {
        seen.add(f.name);
        out.push(f.name);
      }
    }
  }
  return out;
}
