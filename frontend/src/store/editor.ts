import { applyEdgeChanges, applyNodeChanges, type Connection, type EdgeChange, type NodeChange } from "@xyflow/react";
import { create } from "zustand";
import { ApiError, api, currentEmail, subscribeRun } from "../api/client";
import { onConnection } from "../api/connection";
import type { Analysis, FlowDoc, FlowRecord, FlowSummary, NodeResult, RunEvent, RunStatus, ModifierInfo } from "../api/types";
import { rollupComponents } from "../model/rollup";
import { pickPath } from "../platform";
import { useUi } from "./ui";
import {
  applyCollapsed,
  fromFlowDoc,
  idPrefix,
  isBookmark,
  isEtl,
  isNote,
  layoutBookmarks,
  nextId,
  toFlowDoc,
  type AppEdge,
  type AppNode,
  type EtlNode,
  type FlowMeta,
} from "../model/graph";
import { t } from "../i18n";

interface Snapshot {
  nodes: AppNode[];
  edges: AppEdge[];
  meta: FlowMeta;
}

export interface RunState {
  id: string | null;
  status: RunStatus | "idle";
  error: string | null;
  nodes: Record<string, Partial<NodeResult>>;
  counts: Record<string, number>;
  logs: RunEvent[];
  cache: boolean;
}

const IDLE_RUN: RunState = { id: null, status: "idle", error: null, nodes: {}, counts: {}, logs: [], cache: true };
const FINAL: RunStatus[] = ["done", "error", "cancelled", "interrupted"];
const CLIP_MARK = "etl-flow-clipboard";

export type Notice = { kind: "info" | "error"; text: string } | null;

interface EditorState {
  palette: ModifierInfo[];
  paletteMap: Map<string, ModifierInfo>;
  pluginErrors: Record<string, string>;
  flows: FlowSummary[];

  flowId: string | null;
  flowPath: string | null; // flux en fichiers : son fichier (le flux porte le nom de ce fichier)
  revision: number | string | null;
  meta: FlowMeta;
  nodes: AppNode[];
  edges: AppEdge[];
  dirty: boolean;
  analysis: Analysis | null;
  past: Snapshot[];
  future: Snapshot[];
  clipboard: string | null;
  run: RunState;
  notice: Notice;
  caching: boolean; // mise en cache des données des runs (réglage global, retenu d'une session à l'autre)

  loadPalette: () => Promise<void>;
  loadFlows: () => Promise<void>;
  newFlow: () => void;
  openFlow: (id: string) => Promise<void>;
  /** Flux en fichiers : ouvre un fichier de flux par son chemin. */
  openFile: (path: string) => Promise<void>;
  save: () => Promise<boolean>;
  /** « Enregistrer sous… » : demande un emplacement et crée un nouveau fichier. */
  saveAs: () => Promise<boolean>;
  setMeta: (patch: Partial<FlowMeta>) => void;
  setNotice: (n: Notice) => void;
  setCaching: (on: boolean) => void;

  onNodesChange: (changes: NodeChange<AppNode>[]) => void;
  onEdgesChange: (changes: EdgeChange<AppEdge>[]) => void;
  onConnect: (c: Connection) => void;
  beginDrag: () => void;
  addNode: (type: string, position: { x: number; y: number }) => string | null;
  addNote: (position: { x: number; y: number }) => void;
  updateNodeData: (id: string, patch: Record<string, unknown>, coalesce?: string) => void;
  updateParams: (id: string, params: Record<string, unknown>) => void;
  toggleEnabled: () => void;
  removeSelected: () => void;
  select: (ids: string[]) => void;
  addBookmark: () => void;
  updateBookmark: (id: string, patch: Record<string, unknown>) => void;
  align: (kind: "left" | "top" | "hcenter" | "vcenter" | "hdist" | "vdist") => void;

  applyFlowDoc: (doc: FlowDoc) => void;
  openDraft: (doc: FlowDoc) => void;
  undo: () => void;
  redo: () => void;
  copy: () => Promise<void>;
  paste: () => Promise<void>;

  startRun: (opts?: { target?: string | null; fresh?: boolean; params?: Record<string, string> }) => Promise<void>;
  startBatch: (opts: { param: string; pattern?: string; values?: string[]; params?: Record<string, string> }) => Promise<number>;
  openRun: (id: string) => Promise<void>;
  retryRun: (id: string, resume: boolean) => Promise<void>;
  attachRun: (id: string) => Promise<void>;
  cancelRun: () => Promise<void>;
}

// ---- utilitaires hors état ------------------------------------------------------------------

let analysisTimer: ReturnType<typeof setTimeout> | undefined;
let analysisSeq = 0;
let lastCommitKey = "";
let lastCommitAt = 0;
let unsubscribe: (() => void) | undefined;
let eventBuffer: RunEvent[] = [];
let flushTimer: ReturnType<typeof setTimeout> | undefined;

const select = (nodes: AppNode[], ids: Set<string>): AppNode[] => nodes.map((n) => (n.selected === ids.has(n.id) ? n : { ...n, selected: ids.has(n.id) }));

function reduceEvent(run: RunState, e: RunEvent): RunState {
  const next = reduceRaw(run, e);
  return e.type === "node" || e.type === "run_started" ? { ...next, nodes: rollupComponents(next.nodes) } : next;
}

function reduceRaw(run: RunState, e: RunEvent): RunState {
  switch (e.type) {
    case "run_started":
      return { ...run, status: "running", nodes: Object.fromEntries((e.nodes ?? []).map((id) => [id, { status: "pending" as const }])) };
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

/** `wanted`, ou plus bas s'il tombe sur un nœud : deux ajouts au même endroit (centre de la vue) ne s'empilent pas. */
function freeSpot(nodes: AppNode[], wanted: { x: number; y: number }): { x: number; y: number } {
  const W = 220; // largeur probable d'un nœud neuf (il s'élargit selon ses ports), hauteur d'un nœud simple
  const H = 80;
  let pos = { ...wanted };
  for (let i = 0; i < 50; i++) {
    const hit = nodes.find((n) => {
      if (n.type === "bookmark") return false;
      const w = n.measured?.width ?? W;
      const h = n.measured?.height ?? H;
      return pos.x < n.position.x + w && n.position.x < pos.x + W && pos.y < n.position.y + h && n.position.y < pos.y + H;
    });
    if (!hit) break;
    pos = { x: pos.x, y: hit.position.y + (hit.measured?.height ?? H) + 30 };
  }
  return pos;
}

/** Mise en cache retenue (défaut : activée). */
function savedCaching(): boolean {
  try {
    return localStorage.getItem("etl.caching") !== "0";
  } catch {
    return true;
  }
}

const fileName = (name: string) => `${name.replace(/[<>:"/\\|?*]+/g, "_").trim() || "flux"}.json`;

/** Enregistrement demandé pendant une coupure du moteur : refait à son retour (`path` : emplacement déjà choisi). */
let pendingSave: { saveAs: boolean; path: string | null } | null = null;

onConnection((online) => {
  if (!online || !pendingSave) return;
  const { saveAs, path } = pendingSave;
  pendingSave = null;
  void persist(saveAs, path);
});

/**
 * Enregistre le flux. Flux en fichiers : un flux neuf (ou « enregistrer sous ») demande son emplacement ; un flux déjà
 * ouvert réécrit son fichier, sauf s'il a changé sur le disque depuis l'ouverture (message du serveur, rien d'écrasé).
 * Moteur injoignable : l'enregistrement est refait dès son retour (rien n'est perdu, la page garde le flux).
 */
async function persist(saveAs: boolean, chosen: string | null = null): Promise<boolean> {
  const { flowId, revision, meta, nodes, edges } = useEditor.getState();
  const doc = toFlowDoc(meta, nodes, edges);
  const files = useUi.getState().flowStorage === "files";
  let path = chosen;
  try {
    let rec: FlowRecord;
    if (flowId && !(saveAs && files)) {
      rec = await api.saveFlow(flowId, doc, revision);
    } else if (files) {
      path ??= await pickPath({ mode: "write", title: t("Enregistrer le flux"), defaultName: fileName(meta.name), extensions: [".json"] });
      if (!path) return false;
      rec = await api.createFlow(doc, path);
    } else {
      rec = await api.createFlow(doc);
    }
    // flux en fichiers : le nom est celui du fichier choisi (le serveur l'a écrit ainsi dans le flux)
    useEditor.setState((s) => ({
      flowId: rec.id,
      flowPath: rec.path ?? null,
      revision: rec.revision,
      meta: files ? { ...s.meta, name: rec.name } : s.meta,
      dirty: false,
      notice: { kind: "info", text: t("Flux enregistré") },
    }));
    history.replaceState(null, "", `#flow=${rec.id}`); // l'URL désigne le flux : retrouvée au rechargement
    void useEditor.getState().loadFlows();
    return true;
  } catch (err) {
    if (err instanceof ApiError && err.status === 0) {
      pendingSave = { saveAs, path };
      useEditor.setState({ notice: { kind: "error", text: t("Le moteur ne répond pas : le flux sera enregistré dès son retour.") } });
      return false;
    }
    const text = err instanceof ApiError ? err.message : t("Enregistrement impossible");
    useEditor.setState({ notice: { kind: "error", text } });
    return false;
  }
}

export const useEditor = create<EditorState>((set, get) => {
  const snapshot = (): Snapshot => ({ nodes: get().nodes, edges: get().edges, meta: get().meta });

  /** Enregistre l'état courant pour « annuler ». `key` regroupe les frappes successives d'un même champ. */
  const commit = (key = "") => {
    const now = Date.now();
    if (key && key === lastCommitKey && now - lastCommitAt < 1000) {
      lastCommitAt = now;
      return;
    }
    lastCommitKey = key;
    lastCommitAt = now;
    set((s) => ({ past: [...s.past.slice(-99), snapshot()], future: [] }));
  };

  const relayout = (nodes: AppNode[]): AppNode[] => applyCollapsed(layoutBookmarks(nodes, get().paletteMap));

  const touch = () => {
    set({ dirty: true });
    scheduleAnalysis();
  };

  const scheduleAnalysis = () => {
    clearTimeout(analysisTimer);
    analysisTimer = setTimeout(async () => {
      const { meta, nodes, edges } = get();
      const seq = ++analysisSeq;
      try {
        const analysis = await api.analyze(toFlowDoc(meta, nodes, edges), defaultParams(meta));
        if (seq === analysisSeq) set({ analysis });
      } catch {
        /* l'analyse est facultative : l'éditeur reste utilisable si l'API ne répond pas */
      }
    }, 400);
  };

  const flushEvents = () => {
    flushTimer = undefined;
    const batch = eventBuffer;
    eventBuffer = [];
    if (batch.length) set((s) => ({ run: batch.reduce(reduceEvent, s.run) }));
  };
  const pushEvent = (e: RunEvent) => {
    if (e.type === "run_finished") announce(e, (n) => set({ notice: n }));
    eventBuffer.push(e);
    flushTimer ??= setTimeout(flushEvents, 120);
  };

  const replace = (nodes: AppNode[], edges: AppEdge[], meta: FlowMeta, extra: Partial<EditorState> = {}) =>
    set({ nodes: relayout(nodes), edges, meta, past: [], future: [], dirty: false, analysis: null, ...extra });

  /** Affiche un flux lu sur le serveur dans l'éditeur, puis rattache son dernier run (« reconnexion »). */
  const show = async (rec: FlowRecord): Promise<void> => {
    const { nodes, edges, meta } = fromFlowDoc(rec.flow);
    replace(nodes, edges, meta, { flowId: rec.id, flowPath: rec.path ?? null, revision: rec.revision, run: IDLE_RUN });
    history.replaceState(null, "", `#flow=${rec.id}`);
    useUi.getState().setView("editor");
    scheduleAnalysis();
    const last = (await api.runs(`?flow_id=${rec.id}&limit=1`))[0];
    if (last) await get().attachRun(last.id);
  };

  const restore = (snap: Snapshot) => {
    set({ nodes: relayout(snap.nodes), edges: snap.edges, meta: snap.meta });
    touch();
  };

  return {
    palette: [],
    paletteMap: new Map(),
    pluginErrors: {},
    flows: [],
    flowId: null,
    flowPath: null,
    revision: null,
    meta: { name: t("Nouveau flux"), description: "", publishedParams: [], variables: [], component: null },
    nodes: [],
    edges: [],
    dirty: false,
    analysis: null,
    past: [],
    future: [],
    clipboard: null,
    run: IDLE_RUN,
    notice: null,
    caching: savedCaching(),

    // ---------------------------------------------------------------- chargement
    loadPalette: async () => {
      const { modifiers, errors } = await api.modifiers();
      set({ palette: modifiers, paletteMap: new Map(modifiers.map((t) => [t.name, t])), pluginErrors: errors });
      if (get().nodes.length) scheduleAnalysis(); // les avertissements sont dans la langue de la palette
    },
    loadFlows: async () => set({ flows: await api.flows() }),
    newFlow: () => {
      unsubscribe?.();
      history.replaceState(null, "", "#new");
      useUi.getState().setView("editor");
      replace([], [], { name: t("Nouveau flux"), description: "", publishedParams: [], variables: [], component: null }, { flowId: null, flowPath: null, revision: null, run: IDLE_RUN });
    },
    openFlow: async (id) => {
      unsubscribe?.();
      await show(await api.flow(id));
    },
    openFile: async (path) => {
      unsubscribe?.();
      await show(await api.openFlowFile(path));
    },
    save: () => persist(false),
    saveAs: () => persist(true),
    setMeta: (patch) => {
      commit(`meta:${Object.keys(patch).join(",")}`);
      set((s) => ({ meta: { ...s.meta, ...patch } }));
      touch();
    },
    setNotice: (notice) => set({ notice }),
    setCaching: (caching) => {
      try {
        localStorage.setItem("etl.caching", caching ? "1" : "0");
      } catch {
        /* préférence non conservée */
      }
      set({ caching });
    },

    // ---------------------------------------------------------------- édition du graphe
    onNodesChange: (changes) => {
      if (changes.some((c) => c.type === "remove")) commit();
      let nodes = applyNodeChanges(changes, get().nodes);
      const removed = new Set(changes.filter((c) => c.type === "remove").map((c) => c.id));
      if (removed.size) {
        nodes = nodes.map((n) =>
          isBookmark(n) ? { ...n, data: { ...n.data, members: n.data.members.filter((m) => !removed.has(m)) } } : n,
        );
      }
      if (changes.some((c) => c.type === "position" || c.type === "dimensions" || c.type === "remove")) nodes = relayout(nodes);
      set({ nodes });
      if (changes.some((c) => c.type === "remove" || (c.type === "position" && c.dragging === false))) touch();
    },
    onEdgesChange: (changes) => {
      if (changes.some((c) => c.type === "remove")) commit();
      set({ edges: applyEdgeChanges(changes, get().edges) });
      if (changes.some((c) => c.type === "remove")) touch();
    },
    onConnect: (c) => {
      if (!c.source || !c.target || c.source === c.target) return;
      const sourceHandle = c.sourceHandle ?? "output";
      const targetHandle = c.targetHandle ?? "input";
      const { edges } = get();
      if (edges.some((e) => e.source === c.source && e.sourceHandle === sourceHandle && e.target === c.target && e.targetHandle === targetHandle)) return;
      commit();
      const id = nextId("l", edges.map((e) => e.id));
      set({ edges: [...edges, { id, source: c.source, sourceHandle, target: c.target, targetHandle, type: "etl", data: { enabled: true } }] });
      touch();
    },
    beginDrag: () => commit(),
    addNode: (type, position) => {
      const info = get().paletteMap.get(type);
      if (!info) return null;
      commit();
      const id = nextId(idPrefix(type), get().nodes.map((n) => n.id));
      const node: EtlNode = {
        id,
        type: "etl",
        position: freeSpot(get().nodes, position),
        selected: true,
        data: { kind: "etl", type, label: "", params: {}, enabled: true, componentVersion: info.component ? (info.latest_version ?? null) : null },
      };
      set((s) => ({ nodes: relayout([...s.nodes.map((n) => (n.selected ? { ...n, selected: false } : n)), node]) }));
      touch();
      return id;
    },
    addNote: (position) => {
      commit();
      const id = nextId("note", get().nodes.map((n) => n.id));
      set((s) => ({ nodes: [...s.nodes, { id, type: "note", position, data: { kind: "note", text: "Annotation" } }] }));
      touch();
    },
    updateNodeData: (id, patch, coalesce) => {
      commit(coalesce ? `${id}:${coalesce}` : "");
      set((s) => ({ nodes: s.nodes.map((n) => (n.id === id ? ({ ...n, data: { ...n.data, ...patch } } as AppNode) : n)) }));
      touch();
    },
    updateParams: (id, params) => get().updateNodeData(id, { params }, "params"),
    toggleEnabled: () => {
      const { nodes, edges } = get();
      const selectedNodes = nodes.filter((n) => n.selected && isEtl(n));
      const selectedEdges = edges.filter((e) => e.selected);
      if (!selectedNodes.length && !selectedEdges.length) return;
      commit();
      const ids = new Set(selectedNodes.map((n) => n.id));
      set({
        nodes: nodes.map((n) => (ids.has(n.id) && isEtl(n) ? { ...n, data: { ...n.data, enabled: !n.data.enabled } } : n)),
        edges: edges.map((e) => (e.selected ? { ...e, data: { enabled: !(e.data?.enabled ?? true) } } : e)),
      });
      touch();
    },
    removeSelected: () => {
      const { nodes, edges } = get();
      const gone = new Set(nodes.filter((n) => n.selected).map((n) => n.id));
      if (!gone.size && !edges.some((e) => e.selected)) return;
      commit();
      const kept = nodes
        .filter((n) => !gone.has(n.id))
        .map((n) => (isBookmark(n) ? { ...n, data: { ...n.data, members: n.data.members.filter((m) => !gone.has(m)) } } : n));
      set({ nodes: relayout(kept), edges: edges.filter((e) => !e.selected && !gone.has(e.source) && !gone.has(e.target)) });
      touch();
    },
    select: (ids) => set((s) => ({ nodes: select(s.nodes, new Set(ids)) })),

    addBookmark: () => {
      const members = get().nodes.filter((n) => n.selected && isEtl(n)).map((n) => n.id);
      if (!members.length) {
        set({ notice: { kind: "info", text: t("Sélectionnez d'abord les nœuds à regrouper") } });
        return;
      }
      commit();
      const id = nextId("bookmark", get().nodes.map((n) => n.id));
      const colors = ["#cfe3ff", "#d8f3dc", "#ffe8cc", "#f3d9fa", "#fff3bf"];
      const count = get().nodes.filter(isBookmark).length;
      const bm: AppNode = {
        id,
        type: "bookmark",
        position: { x: 0, y: 0 },
        zIndex: -1,
        data: { kind: "bookmark", name: "Signet", color: colors[count % colors.length], members, collapsed: false },
      };
      set((s) => ({ nodes: relayout([bm, ...s.nodes]) }));
      touch();
    },
    updateBookmark: (id, patch) => {
      commit(`${id}:bm`);
      set((s) => ({ nodes: relayout(s.nodes.map((n) => (n.id === id && isBookmark(n) ? { ...n, data: { ...n.data, ...patch } } : n))) }));
      touch();
    },
    align: (kind) => {
      const picked = get().nodes.filter((n) => n.selected && !isBookmark(n));
      if (picked.length < 2) return;
      commit();
      const size = (n: AppNode) => ({ w: n.measured?.width ?? 190, h: n.measured?.height ?? 80 });
      const next = new Map<string, { x: number; y: number }>();
      const by = (f: (n: AppNode) => number) => [...picked].sort((a, b) => f(a) - f(b));
      if (kind === "left") picked.forEach((n) => next.set(n.id, { x: Math.min(...picked.map((p) => p.position.x)), y: n.position.y }));
      if (kind === "top") picked.forEach((n) => next.set(n.id, { x: n.position.x, y: Math.min(...picked.map((p) => p.position.y)) }));
      if (kind === "hcenter") {
        const c = picked.reduce((a, n) => a + n.position.x + size(n).w / 2, 0) / picked.length;
        picked.forEach((n) => next.set(n.id, { x: c - size(n).w / 2, y: n.position.y }));
      }
      if (kind === "vcenter") {
        const c = picked.reduce((a, n) => a + n.position.y + size(n).h / 2, 0) / picked.length;
        picked.forEach((n) => next.set(n.id, { x: n.position.x, y: c - size(n).h / 2 }));
      }
      if (kind === "hdist" || kind === "vdist") {
        const horizontal = kind === "hdist";
        const ordered = by((n) => (horizontal ? n.position.x : n.position.y));
        const first = ordered[0];
        const last = ordered[ordered.length - 1];
        const start = horizontal ? first.position.x : first.position.y;
        const end = horizontal ? last.position.x : last.position.y;
        ordered.forEach((n, i) => {
          const pos = start + ((end - start) * i) / (ordered.length - 1);
          next.set(n.id, horizontal ? { x: pos, y: n.position.y } : { x: n.position.x, y: pos });
        });
      }
      set((s) => ({ nodes: relayout(s.nodes.map((n) => (next.has(n.id) ? { ...n, position: next.get(n.id)! } : n))) }));
      touch();
    },

    // ---------------------------------------------------------------- propositions de l'assistant
    /** Remplace le flux par celui que l'assistant propose : une seule modification, annulable (Ctrl+Z). */
    applyFlowDoc: (doc) => {
      const { nodes, edges, meta } = fromFlowDoc(doc);
      commit();
      set({ nodes: relayout(nodes), edges, meta });
      touch();
    },
    /** Ouvre le flux proposé (un composant, par exemple) comme un flux neuf, à enregistrer où l'on veut. */
    openDraft: (doc) => {
      const { nodes, edges, meta } = fromFlowDoc(doc);
      replace(nodes, edges, meta, { flowId: null, flowPath: null, revision: null, run: IDLE_RUN });
      history.replaceState(null, "", "#new");
      useUi.getState().setView("editor");
      set({ dirty: true });
      scheduleAnalysis();
    },

    // ---------------------------------------------------------------- historique, presse-papiers
    undo: () => {
      const { past, future } = get();
      const prev = past[past.length - 1];
      if (!prev) return;
      lastCommitKey = "";
      set({ past: past.slice(0, -1), future: [snapshot(), ...future] });
      restore(prev);
    },
    redo: () => {
      const { past, future } = get();
      const next = future[0];
      if (!next) return;
      lastCommitKey = "";
      set({ past: [...past, snapshot()], future: future.slice(1) });
      restore(next);
    },
    copy: async () => {
      const { nodes, edges } = get();
      const picked = nodes.filter((n) => n.selected && !isBookmark(n));
      if (!picked.length) return;
      const ids = new Set(picked.map((n) => n.id));
      const payload = JSON.stringify({
        [CLIP_MARK]: 1,
        nodes: picked.map((n) => ({ ...n, selected: false })),
        edges: edges.filter((e) => ids.has(e.source) && ids.has(e.target)),
      });
      set({ clipboard: payload });
      try {
        await navigator.clipboard.writeText(payload);
      } catch {
        /* presse-papiers système refusé : la copie interne suffit dans le même onglet */
      }
    },
    paste: async () => {
      let text = get().clipboard;
      try {
        const system = await navigator.clipboard.readText();
        if (system.includes(CLIP_MARK)) text = system;
      } catch {
        /* lecture refusée : on utilise la copie interne */
      }
      if (!text) return;
      let data: { nodes: AppNode[]; edges: AppEdge[] };
      try {
        data = JSON.parse(text) as { nodes: AppNode[]; edges: AppEdge[] };
        if (!Array.isArray(data.nodes)) return;
      } catch {
        return;
      }
      commit();
      const taken = new Set(get().nodes.map((n) => n.id));
      const takenEdges = new Set(get().edges.map((e) => e.id));
      const rename = new Map<string, string>();
      const pasted: AppNode[] = data.nodes.map((n) => {
        const prefix = isEtl(n) ? idPrefix(n.data.type) : "note";
        const id = nextId(prefix, taken);
        taken.add(id);
        rename.set(n.id, id);
        return { ...n, id, selected: true, position: { x: n.position.x + 40, y: n.position.y + 40 } } as AppNode;
      });
      const edges: AppEdge[] = data.edges.map((e) => {
        const id = nextId("l", takenEdges);
        takenEdges.add(id);
        return { ...e, id, selected: false, source: rename.get(e.source)!, target: rename.get(e.target)! };
      });
      set((s) => ({ nodes: relayout([...s.nodes.map((n) => (n.selected ? { ...n, selected: false } : n)), ...pasted]), edges: [...s.edges, ...edges] }));
      touch();
    },

    // ---------------------------------------------------------------- exécution
    startRun: async ({ target = null, fresh = false, params } = {}) => {
      const { meta, nodes, edges, flowId, run, caching } = get();
      // mise en cache : les nœuds inchangés depuis le dernier run sont repris, seuls les autres tournent
      const reuse = caching && !fresh && run.id && run.cache ? run.id : null;
      const doc = toFlowDoc(meta, nodes, edges);
      try {
        const created = await api.createRun({
          flow: doc,
          flow_id: flowId,
          params: params ?? defaultParams(meta),
          cache: caching,
          target,
          reuse_from: reuse,
          notify_email: currentEmail() || null,
        });
        void askDesktopNotifications();
        await get().attachRun(created.id);
      } catch (err) {
        if (err instanceof ApiError && err.detail && typeof err.detail === "object" && "issues" in err.detail) {
          const issues = (err.detail as { issues: { node: string | null; message: string }[] }).issues;
          set({ notice: { kind: "error", text: t("Lancement refusé : {{issues}}", { issues: issues.map((i) => `${i.node ?? ""} ${i.message}`).join(" ; ") }) } });
        } else {
          set({ notice: { kind: "error", text: err instanceof Error ? err.message : t("Lancement impossible") } });
        }
      }
    },
    startBatch: async ({ param, pattern, values, params }) => {
      const { meta, nodes, edges, flowId } = get();
      const created = await api.createBatch({
        flow: toFlowDoc(meta, nodes, edges),
        flow_id: flowId,
        param,
        pattern,
        values,
        params: params ?? defaultParams(meta),
        cache: true,
        notify_email: currentEmail() || null,
      });
      void askDesktopNotifications();
      set({ notice: { kind: "info", text: t("{{count}} runs mis en file (lot)", { count: created.runs.length }) } });
      return created.runs.length;
    },
    openRun: async (id) => {
      if (get().dirty && !confirm(t("Des modifications non enregistrées seront perdues. Ouvrir le flux de ce run ?"))) return;
      const detail = await api.run(id);
      const { nodes, edges, meta } = fromFlowDoc(detail.flow);
      // Le flux tel qu'il a été exécuté, pas le flux enregistré aujourd'hui : on ne le rattache pas au flux d'origine.
      replace(nodes, edges, { ...meta, name: t("{{name}} (run {{id}})", { name: meta.name, id: id.slice(0, 6) }) }, { flowId: null, flowPath: null, revision: null });
      history.replaceState(null, "", "#new");
      useUi.getState().setView("editor");
      scheduleAnalysis();
      await get().attachRun(id);
    },
    retryRun: async (id, resume) => {
      try {
        const created = await api.retryRun(id, resume);
        await get().openRun(created.id);
      } catch (err) {
        set({ notice: { kind: "error", text: err instanceof Error ? err.message : t("Relance impossible") } });
      }
    },
    attachRun: async (id) => {
      unsubscribe?.();
      eventBuffer = [];
      const detail = await api.run(id);
      const past = await api.runEvents(id, 0);
      let state: RunState = { ...IDLE_RUN, id, status: detail.status, error: detail.error, cache: detail.options.cache ?? true };
      for (const e of past) state = reduceEvent(state, e);
      state = { ...state, status: detail.status, error: detail.error };
      set({ run: state });
      if (!FINAL.includes(detail.status)) {
        const lastId = past.reduce((m, e) => Math.max(m, e.id ?? 0), 0);
        unsubscribe = subscribeRun(id, lastId, pushEvent, (status) => {
          flushEvents();
          set((s) => ({ run: { ...s.run, status: status as RunStatus } }));
        });
      }
    },
    cancelRun: async () => {
      const id = get().run.id;
      if (id) await api.cancelRun(id);
    },
  };
});

/** Fin de run : message dans l'interface, et notification du bureau si l'onglet est en arrière-plan. */
function announce(e: RunEvent, notify: (n: Notice) => void): void {
  const labels: Record<string, string> = {
    done: t("Run terminé"),
    error: t("Run en échec"),
    cancelled: t("Run annulé"),
    interrupted: t("Run interrompu"),
  };
  const text = labels[e.status ?? ""] ?? t("Run terminé");
  notify({ kind: e.status === "done" ? "info" : "error", text: e.error ? t("{{text}} : {{error}}", { text, error: e.error }) : text });
  try {
    if (document.hidden && Notification.permission === "granted") new Notification("Carriole", { body: text });
  } catch {
    /* notifications du bureau indisponibles */
  }
}

async function askDesktopNotifications(): Promise<void> {
  try {
    if (Notification.permission === "default") await Notification.requestPermission();
  } catch {
    /* navigateur sans notifications */
  }
}

export function defaultParams(meta: FlowMeta): Record<string, string> {
  return Object.fromEntries(meta.publishedParams.map((p) => [p.name, p.default]));
}

export { isNote };
