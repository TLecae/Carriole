import { create } from "zustand";
import { api, ApiError, streamAssistant } from "../api/client";
import type { ActionData, AssistantConfig, AssistantEvent, ChatCard, ChatMessage, FlowDoc, Reference } from "../api/types";
import { t } from "../i18n";
import { toFlowDoc } from "../model/graph";
import { useEditor } from "./editor";

export type ActionStatus = "pending" | "applied" | "dismissed" | "failed" | "expired";

export interface ActionItem {
  id: string;
  name: string;
  data: ActionData | null; // null : proposition d'une session précédente, plus applicable
  status: ActionStatus;
  detail?: string;
  base: string; // le flux au moment de la demande : une proposition ne s'applique pas sur un flux qui a changé
}

export interface ToolItem {
  id: string;
  name: string;
  summary: string;
  ok: boolean;
  running?: boolean;
  input?: Record<string, unknown>;
}

/** Ce que fait l'assistant en ce moment (affiché sous la réponse en cours, avec le temps écoulé). */
export type Phase = "thinking" | "tool" | "writing";

export interface Turn {
  role: "user" | "assistant";
  text: string;
  references: Reference[];
  tools: ToolItem[];
  actions: ActionItem[];
  error?: string;
  phase?: Phase;
  phaseSince?: number; // Date.now() au début de la phase
}

const ACTION_NAMES = new Set(["propose_flow_edits", "run_flow", "write_modifier", "create_component"]);

interface AssistantState {
  open: boolean;
  settingsOpen: boolean;
  config: AssistantConfig | null;
  chats: ChatCard[];
  chatId: string | null;
  turns: Turn[];
  busy: boolean;
  notes: string[]; // sort des propositions, transmis au modèle avec le prochain message
  setOpen: (open: boolean) => void;
  setSettingsOpen: (open: boolean) => void;
  loadConfig: () => Promise<void>;
  loadChats: () => Promise<void>;
  newChat: () => void;
  openChat: (id: string) => Promise<void>;
  deleteChat: (id: string) => Promise<void>;
  send: (text: string, references: Reference[]) => Promise<void>;
  stop: () => void;
  apply: (actionId: string) => Promise<void>;
  dismiss: (actionId: string) => void;
}

let abort: AbortController | null = null;

const currentDoc = (): FlowDoc => {
  const { meta, nodes, edges } = useEditor.getState();
  return toFlowDoc(meta, nodes, edges);
};

/** Ce qui compte pour une proposition : les nœuds, leurs réglages et les liens (pas leur position à l'écran). */
const signature = (doc: FlowDoc): string =>
  JSON.stringify([doc.nodes.map((n) => [n.id, n.type, n.params, n.enabled, n.label]), doc.links.map((l) => [l.from, l.to, l.enabled]), doc.variables ?? {}]);

/** Reconstruit l'affichage depuis les messages enregistrés (au format interne : texte, appels et résultats d'outils). */
export function turnsFromMessages(messages: ChatMessage[]): Turn[] {
  const turns: Turn[] = [];
  const results = new Map<string, { content: string; is_error?: boolean }>();
  for (const m of messages) {
    if (m.role === "user" && Array.isArray(m.content)) {
      for (const b of m.content) if (b.type === "tool_result") results.set(b.tool_use_id, { content: b.content, is_error: b.is_error });
    }
  }
  for (const m of messages) {
    if (m.role === "user") {
      if (typeof m.content === "string") {
        turns.push({ role: "user", text: m.ui?.text ?? m.content, references: m.ui?.references ?? [], tools: [], actions: [] });
      }
      continue;
    }
    const blocks = typeof m.content === "string" ? [{ type: "text" as const, text: m.content }] : m.content;
    let turn = turns[turns.length - 1];
    if (!turn || turn.role !== "assistant") {
      turn = { role: "assistant", text: "", references: [], tools: [], actions: [] };
      turns.push(turn);
    }
    for (const b of blocks) {
      if (b.type === "text") turn.text += (turn.text ? "\n\n" : "") + b.text;
      else if (b.type === "tool_use") {
        if (ACTION_NAMES.has(b.name)) turn.actions.push({ id: b.id, name: b.name, data: null, status: "expired", base: "" });
        else turn.tools.push({ id: b.id, name: b.name, summary: b.name, ok: !results.get(b.id)?.is_error, input: b.input });
      }
    }
  }
  return turns;
}

const errorText = (err: unknown): string => (err instanceof Error ? err.message : String(err));

export const useAssistant = create<AssistantState>((set, get) => {
  const patchLast = (fn: (turn: Turn) => Turn) =>
    set((s) => ({ turns: s.turns.map((turn, i) => (i === s.turns.length - 1 ? fn(turn) : turn)) }));
  const patchAction = (id: string, patch: Partial<ActionItem>) =>
    set((s) => ({ turns: s.turns.map((turn) => ({ ...turn, actions: turn.actions.map((a) => (a.id === id ? { ...a, ...patch } : a)) })) }));
  const findAction = (id: string): ActionItem | undefined => get().turns.flatMap((turn) => turn.actions).find((a) => a.id === id);
  const note = (text: string) => set((s) => ({ notes: [...s.notes, text] }));

  return {
    open: false,
    settingsOpen: false,
    config: null,
    chats: [],
    chatId: null,
    turns: [],
    busy: false,
    notes: [],
    setOpen: (open) => {
      set({ open });
      if (open) {
        void get().loadConfig();
        void get().loadChats();
      }
    },
    setSettingsOpen: (settingsOpen) => set({ settingsOpen }),
    loadConfig: async () => {
      try {
        set({ config: await api.assistantConfig() });
      } catch (err) {
        useEditor.getState().setNotice({ kind: "error", text: errorText(err) });
      }
    },
    loadChats: async () => {
      try {
        set({ chats: await api.assistantChats() });
      } catch {
        /* liste facultative */
      }
    },
    newChat: () => {
      abort?.abort();
      set({ chatId: null, turns: [], notes: [], busy: false });
    },
    openChat: async (id) => {
      abort?.abort();
      const chat = await api.assistantChat(id);
      set({ chatId: id, turns: turnsFromMessages(chat.messages), notes: [], busy: false });
    },
    deleteChat: async (id) => {
      await api.assistantDeleteChat(id);
      if (get().chatId === id) get().newChat();
      await get().loadChats();
    },
    stop: () => abort?.abort(),

    send: async (text, references) => {
      const { config, busy } = get();
      if (busy || !text.trim()) return;
      const provider = config?.providers.find((p) => p.id === (config.default ?? "")) ?? config?.providers[0];
      if (!provider) {
        set({ settingsOpen: true });
        return;
      }
      let chatId = get().chatId;
      const doc = currentDoc();
      const base = signature(doc);
      set((s) => ({
        busy: true,
        turns: [
          ...s.turns,
          { role: "user", text, references, tools: [], actions: [] },
          { role: "assistant", text: "", references: [], tools: [], actions: [] },
        ],
      }));
      abort = new AbortController();
      try {
        if (!chatId) {
          const chat = await api.assistantCreateChat(provider.id, provider.model);
          chatId = chat.id;
          set({ chatId });
        }
        let separate = false; // un nouveau morceau de texte après un outil ou une proposition commence un paragraphe
        const phase = (turn: Turn, next: Phase): Turn => (turn.phase === next ? turn : { ...turn, phase: next, phaseSince: Date.now() });
        const onEvent = (e: AssistantEvent) => {
          if (e.type === "thinking") {
            patchLast((turn) => phase(turn, "thinking"));
          } else if (e.type === "text") {
            patchLast((turn) => ({ ...phase(turn, "writing"), text: turn.text + (separate && turn.text ? "\n\n" : "") + e.delta }));
            separate = false;
          } else if (e.type === "tool_start") {
            separate = true;
            const tool = { id: e.id, name: e.name, summary: e.summary, ok: true, running: true, input: e.input };
            patchLast((turn) => ({ ...phase(turn, "tool"), tools: [...turn.tools, tool] }));
          } else if (e.type === "tool") {
            separate = true;
            const done = { id: e.id, name: e.name, summary: e.summary, ok: e.ok, running: false, input: e.input };
            patchLast((turn) => ({
              ...turn,
              tools: turn.tools.some((x) => x.id === e.id) ? turn.tools.map((x) => (x.id === e.id ? done : x)) : [...turn.tools, done],
            }));
          } else if (e.type === "action") {
            separate = true;
            patchLast((turn) => ({ ...turn, actions: [...turn.actions, { id: e.id, name: e.name, data: e.data, status: "pending", base }] }));
          } else if (e.type === "error") {
            patchLast((turn) => ({ ...turn, error: e.message }));
          }
        };
        const { run } = useEditor.getState();
        const notes = get().notes;
        set({ notes: [] });
        await streamAssistant(chatId, { text, references, flow: doc, run_id: run.id ?? null, notes }, onEvent, abort.signal);
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          patchLast((turn) => ({ ...turn, error: err instanceof ApiError ? err.message : errorText(err) }));
        }
      } finally {
        patchLast((turn) => ({ ...turn, phase: undefined, tools: turn.tools.map((x) => (x.running ? { ...x, running: false } : x)) }));
        set({ busy: false });
        abort = null;
        void get().loadChats();
      }
    },

    dismiss: (id) => {
      const action = findAction(id);
      if (!action || action.status !== "pending") return;
      patchAction(id, { status: "dismissed" });
      note(`The user dismissed your proposal (${action.name}).`);
    },

    apply: async (id) => {
      const action = findAction(id);
      if (!action || action.status !== "pending" || !action.data) return;
      const editor = useEditor.getState();
      const done = (detail?: string) => {
        patchAction(id, { status: "applied", detail });
        note(`The user applied your proposal (${action.name}).${detail ? ` ${detail}` : ""}`);
      };
      const fail = (detail: string) => {
        patchAction(id, { status: "failed", detail });
        note(`Your proposal (${action.name}) could not be applied: ${detail}`);
      };
      try {
        if (action.name === "propose_flow_edits") {
          if (signature(currentDoc()) !== action.base) return fail(t("Le flux a changé depuis cette proposition : redemander à l'assistant."));
          editor.applyFlowDoc((action.data as { flow: FlowDoc }).flow);
          done();
        } else if (action.name === "run_flow") {
          const target = (action.data as { until_node: string | null }).until_node;
          await editor.startRun({ target });
          done();
        } else if (action.name === "write_modifier") {
          const d = action.data as { name: string; code: string; test_code: string };
          const result = await api.assistantInstallModifier({ name: d.name, code: d.code, test_code: d.test_code });
          if (!result.installed) return fail(result.output.slice(-1500) || t("Le test a échoué."));
          await editor.loadPalette();
          done(t("Modificateur installé : il est dans la palette."));
        } else if (action.name === "create_component") {
          if (editor.dirty && !confirm(t("Des modifications non enregistrées seront perdues. Ouvrir le composant proposé ?"))) return;
          editor.openDraft((action.data as { flow: FlowDoc }).flow);
          done();
        }
      } catch (err) {
        fail(errorText(err));
      }
    },
  };
});
