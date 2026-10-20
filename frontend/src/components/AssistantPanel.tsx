import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import type { ActionData, Issue, Reference } from "../api/types";
import { t } from "../i18n";
import { isEtl } from "../model/graph";
import { pickPath } from "../platform";
import { type ActionItem, type ToolItem, type Turn, useAssistant } from "../store/assistant";
import { useEditor } from "../store/editor";
import { AssistantSettings } from "./AssistantSettings";
import { Elapsed, Spinner } from "./Spinner";

/** Texte d'une réponse : les blocs ``` sont montrés comme du code, le reste garde ses retours à la ligne. */
function Rich({ text }: { text: string }) {
  const parts = text.split(/```[a-zA-Z]*\n?/);
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <pre key={i} className="ai-code">
            {part.replace(/\n$/, "")}
          </pre>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        ),
      )}
    </>
  );
}

const STATUS: Record<ActionItem["status"], () => string> = {
  pending: () => "",
  applied: () => t("Appliqué"),
  dismissed: () => t("Ignoré"),
  failed: () => t("Échec"),
  expired: () => t("Session précédente"),
};

const TITLES: Record<string, () => string> = {
  propose_flow_edits: () => t("Modifier le flux"),
  run_flow: () => t("Lancer le flux"),
  write_modifier: () => t("Nouveau modifier"),
  create_component: () => t("Nouveau composant"),
};

function ActionCard({ action }: { action: ActionItem }) {
  const { apply, dismiss } = useAssistant();
  const [busy, setBusy] = useState(false);
  const data = action.data as (ActionData & Record<string, unknown>) | null;
  const issues = ((data?.issues as Issue[] | undefined) ?? []).filter((i) => i.level === "error" || i.level === "warning");
  const run = async () => {
    setBusy(true);
    await apply(action.id);
    setBusy(false);
  };
  const label = action.name === "write_modifier" ? t("Tester et installer") : action.name === "run_flow" ? t("Lancer") : action.name === "create_component" ? t("Ouvrir") : t("Appliquer");
  return (
    <div className={`ai-action ${action.status}`} data-testid="ai-action" data-status={action.status}>
      <div className="ai-action-head">
        <strong>{(TITLES[action.name] ?? (() => action.name))()}</strong>
        {action.status !== "pending" && <span className="ai-status">{STATUS[action.status]()}</span>}
      </div>
      {data && typeof data.explanation === "string" && data.explanation && <p>{data.explanation}</p>}
      {data && Array.isArray(data.summary) && (
        <ul className="ai-diff">
          {(data.summary as string[]).map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
      )}
      {issues.length > 0 && (
        <ul className="ai-issues">
          {issues.slice(0, 4).map((i, k) => (
            <li key={k} className={i.level}>
              {i.node ? `${i.node} : ` : ""}
              {i.message}
            </li>
          ))}
        </ul>
      )}
      {action.name === "run_flow" && data && <p>{(data.until_node as string | null) ? t("Jusqu'au nœud {{node}}", { node: data.until_node }) : t("Le flux entier")}</p>}
      {action.name === "write_modifier" && data && (
        <>
          <p>
            <code>{data.name as string}</code>
          </p>
          <details>
            <summary>{t("Code du modificateur")}</summary>
            <pre className="ai-code">{data.code as string}</pre>
          </details>
          <details>
            <summary>{t("Test")}</summary>
            <pre className="ai-code">{data.test_code as string}</pre>
          </details>
          <p className="muted">{t("Le test s'exécute dans un processus isolé ; le plugin n'est installé que s'il réussit. Il tournera ensuite avec vos droits : relisez le code.")}</p>
        </>
      )}
      {action.name === "create_component" && data && <p>{t("{{count}} nœud(s)", { count: ((data.flow as { nodes: unknown[] }).nodes ?? []).length })}</p>}
      {action.detail && <pre className="ai-detail">{action.detail}</pre>}
      {action.status === "pending" && (
        <div className="ai-action-buttons">
          <button className="primary" onClick={() => void run()} disabled={busy} data-testid="ai-apply">
            {busy ? "…" : label}
          </button>
          <button onClick={() => dismiss(action.id)} disabled={busy}>
            {t("Ignorer")}
          </button>
        </div>
      )}
    </div>
  );
}

/** Ce que fait chaque outil de lecture, dit simplement (le nom technique reste en info-bulle). */
const TOOL_LABELS: Record<string, () => string> = {
  list_modifiers: () => t("Liste des modificateurs"),
  describe_modifier: () => t("Lecture d'un modificateur"),
  inspect_file: () => t("Lecture d'un fichier"),
  list_dir: () => t("Parcours d'un dossier"),
  get_flow: () => t("Lecture du flux"),
  analyze_flow: () => t("Analyse du flux"),
  get_run_status: () => t("État du run"),
  get_run_logs: () => t("Journal du run"),
  get_port_sample: () => t("Lecture d'un port"),
};

/** L'argument parlant d'un appel d'outil : modificateur, fichier, nœud… */
function toolTarget(tool: ToolItem): string {
  const input = tool.input ?? {};
  for (const key of ["name", "path", "node", "category", "run_id"]) {
    const value = input[key];
    if (typeof value === "string" && value) return key === "node" && typeof input.port === "string" ? `${value}.${input.port}` : value;
  }
  return "";
}

function ToolSteps({ tools }: { tools: ToolItem[] }) {
  return (
    <ul className="ai-steps" aria-label={t("Outils utilisés")}>
      {tools.map((tool) => (
        <li key={tool.id} className={tool.running ? "running" : tool.ok ? "ok" : "bad"} title={tool.summary}>
          {tool.running ? <Spinner size={11} /> : <span className="ai-step-mark">{tool.ok ? "✓" : "✕"}</span>}
          <span>{(TOOL_LABELS[tool.name] ?? (() => tool.name))()}</span>
          {toolTarget(tool) && <span className="ai-step-target">{toolTarget(tool)}</span>}
        </li>
      ))}
    </ul>
  );
}

/** Indicateur sous la réponse en cours : réflexion (avant le premier mot) ou outil en cours, avec le temps écoulé. */
function PhaseLine({ turn }: { turn: Turn }) {
  if (!turn.phase || turn.phase === "writing" || !turn.phaseSince) return null;
  return (
    <div className="ai-phase" data-testid="ai-phase" data-phase={turn.phase}>
      <Spinner size={12} />
      <span>{turn.phase === "thinking" ? t("Réflexion…") : t("Lecture des informations…")}</span>
      <span className="muted">
        <Elapsed since={turn.phaseSince} />
      </span>
      <SlowHint since={turn.phaseSince} />
    </div>
  );
}

/** Après 20 s sans réponse : le fournisseur charge son modèle ou il est très sollicité. */
function SlowHint({ since }: { since: number }) {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    setSlow(false);
    const timer = window.setTimeout(() => setSlow(true), Math.max(0, since + 20_000 - Date.now()));
    return () => window.clearTimeout(timer);
  }, [since]);
  return slow ? <span className="ai-slow muted">{t("le fournisseur met du temps à répondre (modèle en chargement ou très sollicité)")}</span> : null;
}

function TurnView({ turn, last, busy }: { turn: Turn; last: boolean; busy: boolean }) {
  if (turn.role === "user") {
    return (
      <div className="ai-msg user" data-testid="ai-user">
        <Rich text={turn.text} />
        {turn.references.length > 0 && (
          <div className="ai-refs">
            {turn.references.map((r, i) => (
              <span key={i} className="ai-chip">
                @{r.path ?? (r.port ? `${r.node}.${r.port}` : (r.id ?? r.node))}
              </span>
            ))}
          </div>
        )}
      </div>
    );
  }
  return (
    <div className="ai-msg assistant" data-testid="ai-assistant">
      {turn.tools.length > 0 && <ToolSteps tools={turn.tools} />}
      {turn.text && (
        <div className="ai-text">
          <Rich text={turn.text} />
        </div>
      )}
      {turn.actions.map((a) => (
        <ActionCard key={a.id} action={a} />
      ))}
      {last && busy && <PhaseLine turn={turn} />}
      {turn.error && (
        <p className="error" role="alert">
          {turn.error}
        </p>
      )}
    </div>
  );
}

interface Suggestion {
  label: string;
  hint: string;
  reference: Reference | "file";
}

/** Le « @mot » qui se termine au curseur, s'il y en a un. */
function mentionAt(text: string, caret: number): { start: number; query: string } | null {
  const match = /(^|\s)@([^\s@]*)$/.exec(text.slice(0, caret));
  return match ? { start: caret - match[2].length - 1, query: match[2] } : null;
}

function Composer() {
  const { send, stop, busy, config } = useAssistant();
  const { nodes, paletteMap, run } = useEditor();
  const [text, setText] = useState("");
  const [refs, setRefs] = useState<Reference[]>([]);
  const [caret, setCaret] = useState(0);
  const area = useRef<HTMLTextAreaElement>(null);
  const mention = mentionAt(text, caret);

  const suggestions = useMemo<Suggestion[]>(() => {
    if (!mention) return [];
    const q = mention.query.toLowerCase();
    const out: Suggestion[] = [];
    for (const n of nodes.filter(isEtl)) {
      const info = paletteMap.get(n.data.type);
      out.push({ label: n.id, hint: n.data.label || info?.label || n.data.type, reference: { kind: "node", id: n.id } });
      if (run.id) {
        for (const port of info?.outputs ?? ["output"]) {
          out.push({ label: `${n.id}.${port}`, hint: t("Sortie du dernier run"), reference: { kind: "port", node: n.id, port, run: run.id } });
        }
      }
    }
    out.push({ label: t("Choisir un fichier…"), hint: "", reference: "file" });
    return out.filter((s) => s.reference === "file" || s.label.toLowerCase().includes(q)).slice(0, 8);
  }, [mention, nodes, paletteMap, run.id]);

  const insert = (label: string, reference: Reference) => {
    const start = mention ? mention.start : text.length;
    const end = mention ? caret : text.length;
    const next = `${text.slice(0, start)}@${label} ${text.slice(end)}`;
    setText(next);
    setRefs((r) => [...r, reference]);
    requestAnimationFrame(() => {
      area.current?.focus();
      const pos = start + label.length + 2;
      area.current?.setSelectionRange(pos, pos);
      setCaret(pos);
    });
  };

  const choose = async (s: Suggestion) => {
    if (s.reference !== "file") return insert(s.label, s.reference);
    const path = await pickPath({ mode: "read", title: t("Joindre un fichier") });
    if (path) insert(path, { kind: "file", path });
  };

  const submit = () => {
    const wanted = refs.filter((r) => text.includes(`@${r.path ?? (r.port ? `${r.node}.${r.port}` : (r.id ?? ""))}`));
    void send(text.trim(), wanted);
    setText("");
    setRefs([]);
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    for (const file of Array.from(e.dataTransfer.files)) {
      const path = window.etlDesktop?.pathForFile?.(file);
      if (path) insert(path, { kind: "file", path });
      else useEditor.getState().setNotice({ kind: "info", text: t("Le glisser-déposer de fichiers n'est disponible que dans l'application de bureau : utiliser @ puis « Choisir un fichier… ».") });
    }
  };

  const noProvider = !config || config.providers.length === 0;
  return (
    <div className="ai-composer" onDragOver={(e) => e.preventDefault()} onDrop={onDrop}>
      {suggestions.length > 0 && (
        <ul className="ai-suggest" role="listbox" aria-label={t("Références")}>
          {suggestions.map((s) => (
            <li key={s.label}>
              <button type="button" role="option" onMouseDown={(e) => e.preventDefault()} onClick={() => void choose(s)}>
                <strong>{s.label}</strong> <span className="muted">{s.hint}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <textarea
        ref={area}
        value={text}
        rows={3}
        aria-label={t("Message à l'assistant")}
        placeholder={t("Décrire ce que vous voulez faire… (@ pour joindre un nœud, un port ou un fichier)")}
        disabled={noProvider}
        onChange={(e) => {
          setText(e.target.value);
          setCaret(e.target.selectionStart);
        }}
        onSelect={(e) => setCaret(e.currentTarget.selectionStart)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && suggestions.length === 0 && text.trim() && !busy) {
            e.preventDefault();
            submit();
          }
        }}
      />
      <div className="ai-composer-bar">
        <button type="button" onClick={() => void choose({ label: "", hint: "", reference: "file" })} title={t("Joindre un fichier")} aria-label={t("Joindre un fichier")}>
          📎
        </button>
        <span className="grow" />
        {busy ? (
          <button onClick={stop}>{t("Arrêter")}</button>
        ) : (
          <button className="primary" onClick={submit} disabled={!text.trim() || noProvider} data-testid="ai-send">
            {t("Envoyer")}
          </button>
        )}
      </div>
    </div>
  );
}

export function AssistantPanel() {
  const a = useAssistant();
  const list = useRef<HTMLDivElement>(null);
  const provider = a.config?.providers.find((p) => p.id === a.config?.default) ?? a.config?.providers[0];

  useEffect(() => {
    list.current?.scrollTo({ top: list.current.scrollHeight });
  }, [a.turns]);

  const privacy = provider
    ? provider.privacy === "sample"
      ? t("Schémas et {{count}} lignes d'exemple transmis", { count: provider.sample_rows })
      : t("Schémas seulement : aucune valeur de donnée transmise")
    : "";

  return (
    <aside className="assistant" data-testid="assistant" aria-label={t("Assistant")}>
      <div className="ai-head">
        <strong>{t("Assistant")}</strong>
        <select
          aria-label={t("Conversations")}
          value={a.chatId ?? ""}
          onChange={(e) => (e.target.value ? void a.openChat(e.target.value) : a.newChat())}
        >
          <option value="">{t("Nouvelle conversation")}</option>
          {a.chats.map((c) => (
            <option key={c.id} value={c.id}>
              {c.title || c.id.slice(0, 6)}
            </option>
          ))}
        </select>
        {a.chatId && (
          <button aria-label={t("Supprimer la conversation")} title={t("Supprimer la conversation")} onClick={() => void a.deleteChat(a.chatId!)}>
            🗑
          </button>
        )}
        <button aria-label={t("Réglages de l'assistant")} title={t("Réglages de l'assistant")} onClick={() => a.setSettingsOpen(true)}>
          ⚙
        </button>
        <button aria-label={t("Fermer")} title={t("Fermer")} onClick={() => a.setOpen(false)}>
          ✕
        </button>
      </div>
      {provider && (
        <div className="ai-privacy" data-testid="ai-privacy" data-mode={provider.privacy}>
          🔒 {privacy}
          <span className="muted">
            {" "}
            · {provider.label}
            {provider.model ? ` · ${provider.model}` : ""}
          </span>
        </div>
      )}
      <div className="ai-messages" ref={list}>
        {a.config && a.config.providers.length === 0 ? (
          <div className="ai-empty">
            <p>{t("L'assistant lit votre flux, vos fichiers et vos runs, et propose des modifications que vous appliquez d'un clic.")}</p>
            <button className="primary" onClick={() => a.setSettingsOpen(true)}>
              {t("Configurer un fournisseur")}
            </button>
          </div>
        ) : a.turns.length === 0 ? (
          <div className="ai-empty muted">
            <p>{t("Exemples : « lis communes.gpkg, garde les communes de plus de 10 000 habitants et écris un CSV », « pourquoi ce run a-t-il échoué ? », « explique ce nœud ».")}</p>
          </div>
        ) : (
          a.turns.map((turn, i) => <TurnView key={i} turn={turn} last={i === a.turns.length - 1} busy={a.busy} />)
        )}
      </div>
      <Composer />
      {a.settingsOpen && <AssistantSettings onClose={() => a.setSettingsOpen(false)} />}
    </aside>
  );
}
