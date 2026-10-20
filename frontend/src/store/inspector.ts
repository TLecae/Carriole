import { create } from "zustand";
import { ApiError, api, exportData } from "../api/client";
import type { ColumnFilter, DataQuery, PortInfo, PortRef, PortStats } from "../api/types";
import { t } from "../i18n";

export const PAGE_SIZE = 100;
export const EMPTY_QUERY: DataQuery = { filters: [], where: "", search: "", sort: [] };

export const keyOf = (t: PortRef) => `${t.run}/${t.node}/${t.port}`;

/** Ce que l'inspecteur lit d'un run : les entités de chaque port de chaque nœud. */
export type RunRows = { id: string | null; nodes: Record<string, { rows?: Record<string, number> } | undefined> };

const MAP_POLL_MS = 1500; // carte en préparation : on redemande où elle en est
export const sameTarget = (a: PortRef | null, b: PortRef | null) => !!a && !!b && keyOf(a) === keyOf(b);

interface InspectorState {
  target: PortRef | null;
  info: PortInfo | null;
  error: string | null;
  loading: boolean; // description du port ou première page en cours de chargement (vue préparée à la demande…)
  query: DataQuery;
  version: number; // augmente à chaque changement de requête : les réponses périmées sont ignorées
  filtered: number;
  columns: string[];
  pages: Record<number, unknown[][]>;
  selectedFid: number | null;
  emptyNode: string | null; // nœud choisi sur le canevas, sans données dans le run affiché
  scrollTo: { index: number; nonce: number } | null;
  stats: PortStats | null;
  showStats: boolean;
  overlays: PortRef[]; // ports superposés sur la carte (en plus du port actif)
  colorBy: string | null;
  mapVisible: boolean;

  setTarget: (t: PortRef | null) => Promise<void>;
  showNode: (run: RunRows, node: string) => void;
  patchQuery: (patch: Partial<DataQuery>) => void;
  toggleSort: (column: string, multi: boolean) => void;
  setFilter: (column: string, filter: ColumnFilter | null) => void;
  loadPage: (page: number) => Promise<void>;
  select: (fid: number | null, from: "table" | "map") => Promise<void>;
  toggleStats: () => Promise<void>;
  toggleOverlay: (t: PortRef) => void;
  setColorBy: (column: string | null) => void;
  setMapVisible: (v: boolean) => void;
  exportView: (format: "csv" | "gpkg" | "parquet") => Promise<void>;
}

const pending = new Set<string>();

export const useInspector = create<InspectorState>((set, get) => {
  const message = (e: unknown) => (e instanceof ApiError ? e.message : e instanceof Error ? e.message : t("Erreur"));

  /** Recharge le compte et la première page après un changement de requête. */
  const refresh = () => {
    set((s) => ({ version: s.version + 1, pages: {}, error: null, loading: true }));
    pending.clear();
    void get().loadPage(0);
  };

  /** (Re)charge la description du port et la première page ; `version` : celle de la demande. */
  const load = async (target: PortRef, version: number) => {
    set({ loading: true });
    try {
      const info = await api.dataInfo(target);
      if (get().version !== version) return;
      set({ info, filtered: info.total });
      if (info.geometry?.map_status === "building") void waitForMap(target);
      await get().loadPage(0);
    } catch (e) {
      if (get().version === version) set({ error: message(e), info: null, loading: false });
    }
  };

  /** La carte se prépare en arrière-plan (la table est déjà là) : on suit son état jusqu'à ce qu'elle soit prête. */
  const waitForMap = async (target: PortRef) => {
    for (;;) {
      await new Promise((r) => setTimeout(r, MAP_POLL_MS));
      if (!sameTarget(get().target, target)) return;
      let info: PortInfo;
      try {
        info = await api.dataInfo(target);
      } catch {
        continue; // serveur momentanément injoignable : on réessaie
      }
      const current = get().info;
      if (!sameTarget(get().target, target) || !current) return;
      if (info.geometry?.map_status !== "building") {
        set({ info: { ...current, geometry: info.geometry } });
        return;
      }
    }
  };

  return {
    target: null,
    info: null,
    error: null,
    loading: false,
    query: EMPTY_QUERY,
    version: 0,
    filtered: 0,
    columns: [],
    pages: {},
    selectedFid: null,
    emptyNode: null,
    scrollTo: null,
    stats: null,
    showStats: false,
    overlays: [],
    colorBy: null,
    mapVisible: true,

    showNode: (run, node) => {
      if (!run.id) return;
      const ports = Object.entries(run.nodes[node]?.rows ?? {}).filter(([, n]) => n > 0);
      const port = (ports.find(([p]) => p !== "Rejected") ?? ports[0])?.[0];
      if (!port) {
        set({ emptyNode: node });
        return;
      }
      set({ emptyNode: null });
      const current = get().target;
      if (current && current.run === run.id && current.node === node) return; // déjà ce nœud (peut-être un autre port)
      void get().setTarget({ run: run.id, node, port });
    },

    setTarget: async (target) => {
      if (target) set({ emptyNode: null });
      if (sameTarget(target, get().target)) return;
      pending.clear();
      set((s) => ({
        target,
        info: null,
        error: null,
        query: EMPTY_QUERY,
        version: s.version + 1,
        filtered: 0,
        columns: [],
        pages: {},
        selectedFid: null,
        stats: null,
        showStats: false,
        colorBy: null,
        loading: false,
      }));
      if (!target) return;
      await load(target, get().version);
    },

    patchQuery: (patch) => {
      set((s) => ({ query: { ...s.query, ...patch } }));
      refresh();
    },

    toggleSort: (column, multi) => {
      const current = get().query.sort;
      const found = current.find((k) => k.column === column);
      let next = multi ? current.filter((k) => k.column !== column) : [];
      if (!found) next = [...next, { column, desc: false }];
      else if (!found.desc) next = [...next, { column, desc: true }];
      get().patchQuery({ sort: next });
    },

    setFilter: (column, filter) => {
      const others = get().query.filters.filter((f) => f.column !== column);
      get().patchQuery({ filters: filter ? [...others, filter] : others });
    },

    loadPage: async (page) => {
      const { target, version, query, pages } = get();
      const key = `${version}:${page}`;
      if (!target || pages[page] || pending.has(key)) return;
      pending.add(key);
      try {
        const res = await api.dataRows(target, query, page * PAGE_SIZE, PAGE_SIZE);
        if (get().version !== version) return;
        set((s) => ({ pages: { ...s.pages, [page]: res.rows }, columns: res.columns, filtered: res.filtered, loading: page === 0 ? false : s.loading }));
      } catch (e) {
        if (get().version === version) set({ error: message(e), loading: false });
      } finally {
        pending.delete(key);
      }
    },

    select: async (fid, from) => {
      set({ selectedFid: fid });
      const { target, query } = get();
      if (from === "map" && fid !== null && target) {
        try {
          const { index } = await api.dataLocate(target, query, fid);
          if (index !== null) set({ scrollTo: { index, nonce: Date.now() } });
        } catch {
          /* la ligne reste simplement hors de la fenêtre affichée */
        }
      }
    },

    toggleStats: async () => {
      const { showStats, target, stats } = get();
      set({ showStats: !showStats });
      if (!showStats && target && !stats) {
        try {
          set({ stats: await api.dataStats(target, true) });
        } catch (e) {
          set({ error: message(e) });
        }
      }
    },

    toggleOverlay: (t) =>
      set((s) => ({ overlays: s.overlays.some((o) => sameTarget(o, t)) ? s.overlays.filter((o) => !sameTarget(o, t)) : [...s.overlays, t] })),
    setColorBy: (colorBy) => set({ colorBy }),
    setMapVisible: (mapVisible) => set({ mapVisible }),

    exportView: async (format) => {
      const { target, query } = get();
      if (!target) return;
      try {
        await exportData(target, query, format);
      } catch (e) {
        set({ error: message(e) });
      }
    },
  };
});
