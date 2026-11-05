import { Handle, NodeToolbar, Position, type NodeProps } from "@xyflow/react";
import { memo } from "react";
import { staleOf, useEditor } from "../store/editor";
import type { EtlNode } from "../model/graph";
import type { ModifierInfo } from "../api/types";
import { formatCount, formatNumber, t } from "../i18n";
import { requestRun } from "../store/run";
import { useUi } from "../store/ui";
import { Icon } from "./Icon";

/** Couleur des catégories intégrées (clé : `category_id`, le français du code, sans accents) : des teintes éloignées
 * deux à deux, assez foncées pour le texte blanc de l'en-tête (contraste 4,5 au moins). */
const CATEGORY_COLORS: Record<string, string> = {
  Lecture: "#1d5fc4", // bleu
  Ecriture: "#6d3fb0", // violet
  "Entrees / sorties": "#475569", // ardoise
  Attributs: "#117a55", // vert
  Filtres: "#c2410c", // orange
  Geometrie: "#b5245f", // framboise
  Jointures: "#5f7f0e", // olive
  Calculs: "#7a4b26", // brun
  Rasters: "#8f6700", // ocre
  "Lots et variables": "#1f2a44", // bleu nuit
  Composants: "#0e7490", // cyan
};

const withoutAccents = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "");

export function categoryColor(category: string): string {
  const known = CATEGORY_COLORS[withoutAccents(category)];
  if (known) return known;
  let h = 0; // catégorie d'un plugin : une teinte tirée de son nom
  for (const ch of category) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return `hsl(${h} 45% 38%)`;
}

const statusLabel = (status: string): string =>
  ({
    pending: t("en attente"),
    running: t("en cours"),
    done: t("terminé"),
    cached: t("cache"),
    error: t("erreur"),
    cancelled: t("annulé"),
  })[status] ?? "";

/** Nombre d'entités d'un port après un run : visible sans ouvrir l'inspecteur (en direct pendant l'exécution). */
function PortCount({ node, port, input = false }: { node: string; port: string; input?: boolean }) {
  const live = useEditor((s) => (input ? undefined : s.run.counts[`${node}/${port}`]));
  const stored = useEditor((s) => (input ? s.run.nodes[node]?.rows_in?.[port] : s.run.nodes[node]?.rows?.[port]));
  const count = live ?? stored;
  if (count === undefined) return null;
  const rejected = port === "Rejected" && count > 0;
  return (
    <b className={`port-count${rejected ? " rejected" : ""}${count === 0 ? " empty" : ""}`} data-testid={`port-count-${node}-${input ? "in-" : ""}${port}`} title={t("{{count}} entités", { count: formatNumber(count) })}>
      {formatCount(count)}
    </b>
  );
}

/** Clic sur un port : le retient, ou le branche au port retenu de l'autre sens sur un autre nœud. La suite se joue
 * dans le canevas : un clic sur un nœud y branche le port retenu (Échap annule). */
function clickPort(e: React.MouseEvent, node: string, port: string, side: "in" | "out") {
  e.stopPropagation(); // ni sélection du nœud, ni fin du choix par le canevas
  const { pick, setPick } = useUi.getState();
  if (pick?.kind === "connect" && pick.node !== node && pick.side !== side) {
    const out = side === "out" ? { node, port } : { node: pick.node, port: pick.port };
    const into = side === "in" ? { node, port } : { node: pick.node, port: pick.port };
    useEditor.getState().onConnect({ source: out.node, sourceHandle: out.port, target: into.node, targetHandle: into.port });
    setPick(null);
    return;
  }
  const same = pick?.kind === "connect" && pick.node === node && pick.port === port && pick.side === side;
  setPick(same ? null : { kind: "connect", node, port, side });
}

/** Port retenu pour un branchement par clics, sur ce nœud (« out/port » ou « in/port »). */
const usePicked = (node: string) => useUi((s) => (s.pick?.kind === "connect" && s.pick.node === node ? `${s.pick.side}/${s.pick.port}` : null));

/** Attributs d'un port de sortie, d'après l'analyse du flux : dépliés sous le port. */
function PortFields({ node, port }: { node: string; port: string }) {
  const schema = useEditor((s) => s.analysis?.schemas?.[`${node}/${port}`]);
  if (!schema) return null;
  return (
    <ul className="port-fields nowheel" data-testid={`port-fields-${node}-${port}`}>
      {schema.geometry && (
        <li className="geom" title={schema.crs ?? undefined}>
          <span>{t("géométrie")}</span>
          <small>{schema.crs ?? ""}</small>
        </li>
      )}
      {schema.fields.map((f) => (
        <li key={f.name} title={`${f.name} : ${f.type}`}>
          <span>{f.name}</span>
          <small>{f.type}</small>
        </li>
      ))}
      {!schema.fields.length && !schema.geometry && <li className="empty">{t("aucun attribut")}</li>}
    </ul>
  );
}

function OutPort({ node, port, cached, picked }: { node: string; port: string; cached: boolean; picked: boolean }) {
  const key = `${node}/${port}`;
  const known = useEditor((s) => !!s.analysis?.schemas?.[key]);
  const open = useUi((s) => !!s.openPorts[key]) && known;
  return (
    <>
      <div className={`port${port === "Rejected" ? " rejected" : ""}`}>
        {known && (
          <button
            type="button"
            className="port-toggle nodrag"
            aria-expanded={open}
            title={open ? t("Masquer les attributs") : t("Afficher les attributs")}
            data-testid={`port-toggle-${node}-${port}`}
            onClick={(e) => {
              e.stopPropagation();
              useUi.getState().togglePort(key);
            }}
          >
            <Icon name="chevron" size={12} />
          </button>
        )}
        <span>{port}</span>
        <PortCount node={node} port={port} />
        {cached && <i className="cache-dot" title={t("Données en cache : consultables dans l'inspecteur")} />}
        <Handle
          type="source"
          position={Position.Right}
          id={port}
          className={picked ? "picked" : undefined}
          title={t("Glisser vers une entrée, ou cliquer puis cliquer sur le modificateur à brancher")}
          onClick={(e) => clickPort(e, node, port, "out")}
        />
      </div>
      {open && <PortFields node={node} port={port} />}
    </>
  );
}

/** Lecteur dont la lecture est limitée (paramètre marqué `x-etl-row-limit`, « Lignes lues au plus ») : la limite. */
function rowLimit(info: ModifierInfo, params: Record<string, unknown>): number | string | null {
  const props = info.params_schema.properties ?? {};
  const key = Object.keys(props).find((k) => props[k]["x-etl-row-limit"]);
  const value = key === undefined ? undefined : params[key];
  if (typeof value === "number") return value > 0 ? value : null;
  return typeof value === "string" && value.trim() && Number(value) !== 0 ? value.trim() : null; // ${PARAMÈTRE}
}

function EtlNodeViewBase({ id, data, selected }: NodeProps<EtlNode>) {
  const info = useEditor((s) => s.paletteMap.get(data.type));
  const result = useEditor((s) => s.run.nodes[id]);
  const runCache = useEditor((s) => s.run.cache);
  const stale = useEditor((s) => staleOf(s).has(id));
  const livePorts = useEditor((s) => s.analysis?.ports?.[id]);
  const picked = usePicked(id);
  const worst = useEditor((s) => {
    const issues = s.analysis?.issues.filter((i) => i.node === id) ?? [];
    return issues.some((i) => i.level === "error") ? "error" : issues.length ? "warning" : null;
  });

  if (!info) {
    // Modificateur absent de la palette (plugin retiré ?) : le nœud reste visible, en rouge.
    return (
      <div className={`etl-node unknown${selected ? " selected" : ""}`}>
        <div className="node-head" style={{ background: "#b42318" }}>
          {data.label || data.type}
          <small>{t("modifier inconnu")}</small>
        </div>
      </div>
    );
  }

  const status = result?.status;
  const cached = status === "done" || status === "cached";
  const ins = livePorts?.inputs ?? info.inputs; // les ports peuvent dépendre des paramètres du nœud
  const outs = livePorts?.outputs ?? info.outputs;
  const rows = Math.max(ins.length, outs.length, 1);
  const limit = rowLimit(info, data.params);
  const classes = [
    "etl-node",
    selected ? "selected" : "",
    data.enabled ? "" : "disabled",
    status ? `st-${status}` : "",
    stale && status ? "stale" : "",
    worst ? `has-${worst}` : "",
  ].join(" ");

  return (
    <div className={classes} data-testid={`node-${id}`}>
      <NodeActions id={id} enabled={data.enabled} output={outs[0]} />
      <div className="node-head" style={{ background: categoryColor(info.category_id ?? info.category) }}>
        {data.label || info.label}
        {limit !== null && (
          <span
            className="row-limit"
            data-testid={`row-limit-${id}`}
            title={t("Lecture limitée aux {{n}} premières entités (paramètre « Lignes lues au plus »)", { n: typeof limit === "number" ? formatNumber(limit) : limit })}
          >
            ≤ {typeof limit === "number" ? formatCount(limit) : limit}
          </span>
        )}
        <small>{id}</small>
        {info.component && info.flow_id && (
          <button
            type="button"
            className="node-open nodrag"
            title={t("Ouvrir le composant (remplace le flux affiché)")}
            aria-label={t("Ouvrir le composant")}
            onClick={(e) => {
              e.stopPropagation();
              void useEditor.getState().openFlow(info.flow_id!);
            }}
          >
            ↗
          </button>
        )}
        {worst && <span className={`badge ${worst}`} title={t("Voir l'onglet Avertissements")} />}
      </div>
      <div className="node-ports" style={{ minHeight: rows * 22 }}>
        <div className="ports in">
          {ins.map((p) => (
            <div className="port" key={p}>
              <Handle
                type="target"
                position={Position.Left}
                id={p}
                className={picked === `in/${p}` ? "picked" : undefined}
                title={t("Glisser vers une sortie, ou cliquer puis cliquer sur le modificateur à brancher")}
                onClick={(e) => clickPort(e, id, p, "in")}
              />
              <span>{p}</span>
              <PortCount node={id} port={p} input />
            </div>
          ))}
        </div>
        <div className="ports out">
          {outs.map((p) => (
            <OutPort key={p} node={id} port={p} cached={cached && !!runCache} picked={picked === `out/${p}`} />
          ))}
        </div>
      </div>
      {status && statusLabel(status) && (
        <div
          className="node-foot"
          title={status === "cached" && info.source ? t("Source externe reprise du dernier run, ses réglages n'ayant pas changé. « Tout relancer » (menu Exécution) la relit.") : undefined}
        >
          <span className={`dot ${status}`} />
          {statusLabel(status)}
          {result?.seconds != null && status !== "running" && status !== "pending" && ` · ${formatNumber(result.seconds)} s`}
          {result?.rss_mb ? t(" · {{n}} Mo", { n: Math.round(result.rss_mb) }) : ""}
          {stale && (
            <span className="stale-tag" data-testid={`stale-${id}`} title={t("Ce nœud ou un nœud en amont a changé depuis le run : ces résultats ne correspondent plus au flux. Relancer pour les mettre à jour.")}>
              {t(" · à relancer")}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

/** Bandeau au-dessus du nœud sélectionné (seul) : lancer jusqu'à lui, l'activer ou le désactiver (ses données
 * s'ouvrent d'un clic sur le nœud), le brancher à un autre nœud, l'annoter. */
function NodeActions({ id, enabled, output }: { id: string; enabled: boolean; output?: string }) {
  const busy = useEditor((s) => s.run.status === "queued" || s.run.status === "running");
  const stop = (e: React.MouseEvent) => e.stopPropagation(); // le clic ne doit ni désélectionner ni déplacer le nœud
  return (
    <NodeToolbar position={Position.Top} offset={8} className="node-toolbar">
      <button
        type="button"
        className="nodrag"
        disabled={busy || !enabled}
        title={enabled ? t("Lancer le flux jusqu'à ce nœud (reprend le cache des nœuds inchangés)") : t("Nœud désactivé : réactivez-le pour le lancer")}
        aria-label={t("Lancer jusqu'à ce nœud")}
        onMouseDown={stop}
        onClick={(e) => {
          stop(e);
          requestRun(id);
        }}
      >
        <Icon name="runTo" />
      </button>
      <button
        type="button"
        className="nodrag"
        aria-pressed={!enabled}
        title={enabled ? t("Désactiver ce nœud (Alt+D) : il ne tourne plus, ni son aval") : t("Réactiver ce nœud (Alt+D)")}
        aria-label={enabled ? t("Désactiver le nœud") : t("Réactiver le nœud")}
        onMouseDown={stop}
        onClick={(e) => {
          stop(e);
          useEditor.getState().toggleEnabled();
        }}
      >
        <Icon name="power" />
      </button>
      {output && (
        <button
          type="button"
          className="nodrag"
          title={t("Brancher ce nœud : cliquez ensuite sur le modificateur qui recevra sa sortie « {{port}} »", { port: output })}
          aria-label={t("Brancher à un autre modificateur")}
          onMouseDown={stop}
          onClick={(e) => {
            stop(e);
            useUi.getState().setPick({ kind: "connect", node: id, port: output, side: "out" });
          }}
        >
          <Icon name="connect" />
        </button>
      )}
      <button
        type="button"
        className="nodrag"
        title={t("Ajouter une annotation liée à ce nœud : elle le suit quand il est déplacé")}
        aria-label={t("Annoter le nœud")}
        onMouseDown={stop}
        onClick={(e) => {
          stop(e);
          useEditor.getState().addLinkedNote(id);
        }}
      >
        <Icon name="note" />
      </button>
    </NodeToolbar>
  );
}

export const EtlNodeView = memo(EtlNodeViewBase);
