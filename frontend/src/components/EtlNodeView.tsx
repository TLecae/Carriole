import { Handle, NodeToolbar, Position, type NodeProps } from "@xyflow/react";
import { memo } from "react";
import { staleOf, useEditor } from "../store/editor";
import type { EtlNode } from "../model/graph";
import type { ModifierInfo } from "../api/types";
import { formatCount, formatNumber, t } from "../i18n";
import { requestRun } from "../store/run";
import { Icon } from "./Icon";

export function categoryColor(category: string): string {
  let h = 0;
  for (const ch of category) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return `hsl(${h} 45% 42%)`;
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
      <NodeActions id={id} enabled={data.enabled} />
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
              <Handle type="target" position={Position.Left} id={p} />
              <span>{p}</span>
              <PortCount node={id} port={p} input />
            </div>
          ))}
        </div>
        <div className="ports out">
          {outs.map((p) => (
            <div className={`port${p === "Rejected" ? " rejected" : ""}`} key={p}>
              <span>{p}</span>
              <PortCount node={id} port={p} />
              {cached && runCache && <i className="cache-dot" title={t("Données en cache : consultables dans l'inspecteur")} />}
              <Handle type="source" position={Position.Right} id={p} />
            </div>
          ))}
        </div>
      </div>
      {status && statusLabel(status) && (
        <div className="node-foot">
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
 * s'ouvrent d'un clic sur le nœud). Les mêmes actions que le menu Exécution, à portée de clic. */
function NodeActions({ id, enabled }: { id: string; enabled: boolean }) {
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
    </NodeToolbar>
  );
}

export const EtlNodeView = memo(EtlNodeViewBase);
