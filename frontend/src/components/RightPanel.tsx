import { useEffect, useMemo } from "react";
import type { PublishedParam } from "../api/types";
import { isBookmark, isEtl, isNote, upstreamAttributes } from "../model/graph";
import { useEditor } from "../store/editor";
import { useUi } from "../store/ui";
import { ResizeHandle } from "./ResizeHandle";
import { PublishVersions, VersionPicker } from "./ComponentVersions";
import { SchemaForm, type FormCtx } from "./SchemaForm";
import { formatNumber, t } from "../i18n";
import { buildHints } from "../model/variables";

export function RightPanel() {
  const nodes = useEditor((s) => s.nodes);
  const selected = nodes.filter((n) => n.selected);
  const one = selected.length === 1 ? selected[0] : null;
  // la fenêtre redimensionnée ne doit pas laisser le panneau plus large que ce qu'elle permet
  useEffect(() => {
    const onResize = () => useUi.getState().setRightWidth(useUi.getState().rightWidth, false);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return (
    <aside className="right-panel" aria-label={t("Propriétés")}>
      <ResizeHandle edge="left" size={() => useUi.getState().rightWidth} set={useUi.getState().setRightWidth} label={t("Redimensionner le panneau (glisser, ou flèches gauche et droite)")} />
      <div className="right-body">
        {one && isEtl(one) ? <NodePanel id={one.id} /> : one && isBookmark(one) ? <BookmarkPanel id={one.id} /> : one && isNote(one) ? <p className="muted">{t("Annotation : modifiez le texte directement sur le canvas.")}</p> : selected.length > 1 ? <p className="muted">{t("{{count}} éléments sélectionnés.", { count: selected.length })}</p> : <FlowPanel />}
      </div>
    </aside>
  );
}

function NodePanel({ id }: { id: string }) {
  const node = useEditor((s) => s.nodes.find((n) => n.id === id));
  const edges = useEditor((s) => s.edges);
  const analysis = useEditor((s) => s.analysis);
  const info = useEditor((s) => (node && isEtl(node) ? s.paletteMap.get(node.data.type) : undefined));
  const result = useEditor((s) => s.run.nodes[id]);
  const published = useEditor((s) => s.meta.publishedParams);
  const meta = useEditor((s) => s.meta);
  const allNodes = useEditor((s) => s.nodes);
  const { updateNodeData, updateParams } = useEditor.getState();

  const ctx: FormCtx | null = useMemo(
    () =>
      info
        ? {
            defs: info.params_schema.$defs ?? {},
            attributes: upstreamAttributes(id, edges, analysis?.schemas),
            inputAttributes: Object.fromEntries(
              [...new Set(edges.filter((e) => e.target === id).map((e) => e.targetHandle ?? "input"))].map((port) => [port, upstreamAttributes(id, edges, analysis?.schemas, port)]),
            ),
            parameters: published.map((p) => p.name),
            variables: buildHints(meta, allNodes),
          }
        : null,
    [info, id, edges, analysis, published, meta, allNodes],
  );
  if (!node || !isEtl(node)) return null;
  if (!info || !ctx) return <p className="error-text">{t("Modificateur « {{type}} » introuvable (plugin retiré ?).", { type: node.data.type })}</p>;
  const issues = analysis?.issues.filter((i) => i.node === id) ?? [];

  return (
    <div className="node-panel">
      <h2>{info.label}</h2>
      <p className="muted">{info.description}</p>
      <label className="field">
        <span className="field-label">{t("Nom du nœud")}</span>
        <input type="text" value={node.data.label} placeholder={info.label} onChange={(e) => updateNodeData(id, { label: e.target.value }, "label")} />
      </label>
      <div className="inline-fields">
        <label className="field inline">
          <input type="checkbox" checked={node.data.enabled} onChange={(e) => updateNodeData(id, { enabled: e.target.checked })} />
          <span className="field-label">{t("Activé")}</span>
        </label>
      </div>
      {info.component && (
        <VersionPicker id={id} name={info.name.replace(/^component:/, "")} current={node.data.componentVersion ?? null} latest={info.latest_version ?? null} />
      )}
      {issues.map((i, k) => (
        <p key={k} className={i.level === "error" ? "error-text" : "warn-text"}>
          {i.message}
        </p>
      ))}
      <SchemaForm key={id} schema={info.params_schema} value={node.data.params} onChange={(p) => updateParams(id, p)} ctx={ctx} />
      {info.help && (
        <details className="help">
          <summary>{t("Aide")}</summary>
          <p>{info.help}</p>
        </details>
      )}
      {result && (
        <section className="run-info" aria-label={t("Dernier run")}>
          <h3>{t("Dernier run")}</h3>
          <p>
            {result.status}
            {result.seconds != null ? ` · ${formatNumber(result.seconds)} s` : ""}
            {result.rss_mb ? t(" · {{n}} Mo", { n: Math.round(result.rss_mb) }) : ""}
          </p>
          {Object.entries(result.rows ?? {}).map(([p, n]) => (
            <p key={p} className={p === "Rejected" ? "warn-text" : ""}>
              {t("{{port}} : {{count}} entités", { port: p, count: formatNumber(n) })}
            </p>
          ))}
          {Object.entries(result.rows_in ?? {}).map(([p, n]) => (
            <p key={p}>
              {t("{{port}} (entrée) : {{count}} entités", { port: p, count: formatNumber(n) })}
            </p>
          ))}
          {result.error && <p className="error-text">{result.error}</p>}
        </section>
      )}
    </div>
  );
}

function BookmarkPanel({ id }: { id: string }) {
  const node = useEditor((s) => s.nodes.find((n) => n.id === id));
  const update = useEditor.getState().updateBookmark;
  if (!node || !isBookmark(node)) return null;
  return (
    <div className="node-panel">
      <h2>{t("Signet")}</h2>
      <label className="field">
        <span className="field-label">{t("Nom")}</span>
        <input type="text" value={node.data.name} onChange={(e) => update(id, { name: e.target.value })} />
      </label>
      <label className="field">
        <span className="field-label">{t("Couleur")}</span>
        <input type="color" value={node.data.color} onChange={(e) => update(id, { color: e.target.value })} />
      </label>
      <label className="field inline">
        <input type="checkbox" checked={node.data.collapsed} onChange={(e) => update(id, { collapsed: e.target.checked })} />
        <span className="field-label">{t("Replié")}</span>
      </label>
      <p className="muted">{t("{{count}} nœud(s).", { count: node.data.members.length })}</p>
    </div>
  );
}

const PARAM_TYPES: PublishedParam["type"][] = ["text", "number", "bool", "choice", "path", "connection"];

function FlowPanel() {
  const meta = useEditor((s) => s.meta);
  const setMeta = useEditor.getState().setMeta;
  const editVariable = (i: number, patch: Partial<{ name: string; value: string }>) =>
    setMeta({ variables: meta.variables.map((v, j) => (j === i ? { ...v, ...patch } : v)) });
  const edit = (i: number, patch: Partial<PublishedParam>) => setMeta({ publishedParams: meta.publishedParams.map((p, j) => (j === i ? { ...p, ...patch } : p)) });
  return (
    <div className="node-panel">
      <h2>{t("Flux")}</h2>
      <label className="field">
        <span className="field-label">{t("Description")}</span>
        <textarea rows={3} value={meta.description} onChange={(e) => setMeta({ description: e.target.value })} />
      </label>
      <h3>{t("Composant")}</h3>
      <label className="field inline">
        <input
          type="checkbox"
          aria-label={t("Ce flux est un composant")}
          checked={meta.component !== null}
          onChange={(e) =>
            setMeta({
              component: e.target.checked
                ? { name: meta.name.normalize("NFD").replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 60) || "composant", label: meta.name, category: "Composants", description: "" }
                : null,
            })
          }
        />
        <span className="field-label">{t("Ce flux est un composant réutilisable")}</span>
      </label>
      {meta.component ? (
        <>
          <p className="muted">
            {t(
              "Il apparaît dans la palette comme un modificateur. Ses ports sont les nœuds « Entrée du composant » et « Sortie du composant » ; ses paramètres publiés (ci-dessous) deviennent ses paramètres.",
            )}
          </p>
          {(["name", "label", "category"] as const).map((key) => (
            <label className="field" key={key}>
              <span className="field-label">{{ name: t("Identifiant (lettres, chiffres, _ - .)"), label: t("Libellé dans la palette"), category: t("Catégorie") }[key]}</span>
              <input type="text" aria-label={t("Composant : {{key}}", { key })} value={meta.component![key]} onChange={(e) => setMeta({ component: { ...meta.component!, [key]: e.target.value } })} />
            </label>
          ))}
          <PublishVersions name={meta.component.name} />
        </>
      ) : (
        <p className="muted">{t("Cocher pour réutiliser ce flux comme un modificateur dans d'autres flux.")}</p>
      )}
      <h3>{t("Paramètres publiés")}</h3>
      <p className="muted">
        {t("Saisis au lancement. Utilisables dans n'importe quel paramètre de nœud avec")} <code>{"${NOM}"}</code> {t("; variables d'environnement avec")} <code>{"${env:NOM}"}</code>.
      </p>
      {meta.publishedParams.map((p, i) => (
        <div className="row-card" key={i}>
          <div className="cell">
            <span className="cell-label">{t("Nom")}</span>
            <input type="text" value={p.name} aria-label={t("Nom du paramètre {{n}}", { n: i + 1 })} onChange={(e) => edit(i, { name: e.target.value })} />
          </div>
          <div className="cell">
            <span className="cell-label">{t("Libellé")}</span>
            <input type="text" value={p.label} onChange={(e) => edit(i, { label: e.target.value })} />
          </div>
          <div className="cell">
            <span className="cell-label">{t("Type")}</span>
            <select value={p.type} onChange={(e) => edit(i, { type: e.target.value as PublishedParam["type"] })}>
              {PARAM_TYPES.map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </div>
          <div className="cell">
            <span className="cell-label">{t("Valeur par défaut")}</span>
            <input type="text" value={p.default} aria-label={t("Valeur par défaut du paramètre {{n}}", { n: i + 1 })} onChange={(e) => edit(i, { default: e.target.value })} />
          </div>
          <label className="cell inline">
            <input type="checkbox" checked={p.required} onChange={(e) => edit(i, { required: e.target.checked })} />
            <span className="cell-label">{t("Obligatoire")}</span>
          </label>
          <button type="button" className="remove" aria-label={t("Supprimer le paramètre")} onClick={() => setMeta({ publishedParams: meta.publishedParams.filter((_, j) => j !== i) })}>
            ×
          </button>
        </div>
      ))}
      <button type="button" className="add" onClick={() => setMeta({ publishedParams: [...meta.publishedParams, { name: "", label: "", type: "text", default: "", required: false, choices: [] }] })}>
        {t("+ Ajouter un paramètre")}
      </button>
      <h3>{t("Variables du flux")}</h3>
      <p className="muted">
        {t("Des constantes ou des valeurs composées d'autres variables, résolues au début du run. Lues comme les paramètres, avec")}{" "}
        <code>{"${NOM}"}</code>
        {t(". Aussi disponibles :")} <code>{"${sys:date}"}</code>, <code>{"${sys:flow_dir}"}</code>, <code>{"${sys:run_id}"}</code>…{" "}
        {t("et")} <code>{"${var:NOM}"}</code> {t("posée par un nœud « Poser des variables » en amont.")}
      </p>
      {meta.variables.map((v, i) => (
        <div className="row-card" key={i}>
          <div className="cell">
            <span className="cell-label">{t("Nom")}</span>
            <input type="text" value={v.name} aria-label={t("Nom de la variable {{n}}", { n: i + 1 })} onChange={(e) => editVariable(i, { name: e.target.value })} />
          </div>
          <div className="cell">
            <span className="cell-label">{t("Valeur")}</span>
            <input type="text" value={v.value} aria-label={t("Valeur de la variable {{n}}", { n: i + 1 })} onChange={(e) => editVariable(i, { value: e.target.value })} />
          </div>
          <button type="button" className="remove" aria-label={t("Supprimer la variable")} onClick={() => setMeta({ variables: meta.variables.filter((_, j) => j !== i) })}>
            ×
          </button>
        </div>
      ))}
      <button type="button" className="add" onClick={() => setMeta({ variables: [...meta.variables, { name: "", value: "" }] })}>
        {t("+ Ajouter une variable")}
      </button>
    </div>
  );
}
