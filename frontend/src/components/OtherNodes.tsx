import { BaseEdge, getBezierPath, NodeToolbar, Position, useInternalNode, type EdgeProps, type NodeProps } from "@xyflow/react";
import { memo } from "react";
import { useEditor } from "../store/editor";
import { useUi } from "../store/ui";
import type { AppEdge, BookmarkNode, NoteNode } from "../model/graph";
import { t } from "../i18n";
import { Icon } from "./Icon";

export const NoteNodeView = memo(function NoteNodeView({ id, data, selected, parentId }: NodeProps<NoteNode>) {
  const update = useEditor((s) => s.updateNodeData);
  const picking = useUi((s) => s.pick?.kind === "note" && s.pick.note === id);
  const stop = (e: React.MouseEvent) => e.stopPropagation(); // le clic ne doit ni désélectionner ni déplacer l'annotation
  return (
    <div className={`note-node${selected ? " selected" : ""}${parentId ? " linked" : ""}`} data-testid={`note-${id}`}>
      {parentId && <NoteLink id={id} parentId={parentId} />}
      <NodeToolbar position={Position.Top} offset={8} className="node-toolbar">
        {parentId ? (
          <button
            type="button"
            className="nodrag"
            title={t("Délier l'annotation : elle ne suit plus le nœud {{node}}", { node: parentId })}
            aria-label={t("Délier l'annotation")}
            onMouseDown={stop}
            onClick={(e) => {
              stop(e);
              useEditor.getState().linkNote(id, null);
            }}
          >
            <Icon name="unlink" />
          </button>
        ) : (
          <button
            type="button"
            className="nodrag"
            aria-pressed={picking}
            title={t("Lier l'annotation à un modificateur : cliquez ensuite sur le modificateur ; elle le suivra quand il est déplacé")}
            aria-label={t("Lier à un modificateur")}
            onMouseDown={stop}
            onClick={(e) => {
              stop(e);
              useUi.getState().setPick(picking ? null : { kind: "note", note: id });
            }}
          >
            <Icon name="link" />
          </button>
        )}
      </NodeToolbar>
      <textarea
        className="nodrag"
        aria-label={t("Texte de l'annotation")}
        value={data.text}
        onChange={(e) => update(id, { text: e.target.value }, "text")}
      />
    </div>
  );
});

/** Trait pointillé de l'annotation à son nœud, d'un bord à l'autre (rien quand ils se chevauchent). */
function NoteLink({ id, parentId }: { id: string; parentId: string }) {
  const self = useInternalNode(id);
  const parent = useInternalNode(parentId);
  if (!self || !parent) return null;
  const w1 = self.measured.width ?? 0;
  const h1 = self.measured.height ?? 0;
  const w2 = parent.measured.width ?? 0;
  const h2 = parent.measured.height ?? 0;
  // repère de l'annotation : son coin haut gauche
  const x1 = w1 / 2;
  const y1 = h1 / 2;
  const dx = parent.internals.positionAbsolute.x - self.internals.positionAbsolute.x + w2 / 2 - x1;
  const dy = parent.internals.positionAbsolute.y - self.internals.positionAbsolute.y + h2 / 2 - y1;
  // fraction du segment entre centres qui reste dans un rectangle de w × h centré sur son extrémité
  const inside = (w: number, h: number) => Math.min(dx ? w / 2 / Math.abs(dx) : Infinity, dy ? h / 2 / Math.abs(dy) : Infinity);
  const a = inside(w1, h1);
  const b = inside(w2, h2);
  if (a + b >= 1) return null;
  return (
    <svg className="note-link" aria-hidden="true">
      <line x1={x1 + dx * a} y1={y1 + dy * a} x2={x1 + dx * (1 - b)} y2={y1 + dy * (1 - b)} />
    </svg>
  );
}

export const BookmarkView = memo(function BookmarkView({ data, selected }: NodeProps<BookmarkNode>) {
  return (
    <div className={`bookmark${selected ? " selected" : ""}${data.collapsed ? " collapsed" : ""}`} style={{ background: data.color }}>
      <span className="bookmark-title">
        {data.name}
        <small>{data.collapsed ? t(" · {{count}} nœuds repliés", { count: data.members.length }) : ""}</small>
      </span>
    </div>
  );
});

export const EtlEdgeView = memo(function EtlEdgeView(props: EdgeProps<AppEdge>) {
  const { id, sourceHandleId, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, selected, data } = props;
  const sourceHandle = sourceHandleId ?? "output";
  const dropTarget = useUi((s) => s.dropEdge === id);
  // le nombre d'entités est affiché sur le port de sortie (une seule fois : une étiquette au milieu d'une liaison
  // courte tombait sur le port et le nœud)
  const [path] = getBezierPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition });
  const disabled = data?.enabled === false;
  const rejected = sourceHandle === "Rejected";
  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        className={`etl-edge${disabled ? " disabled" : ""}${rejected ? " rejected" : ""}${selected ? " selected" : ""}${dropTarget ? " drop-target" : ""}`}
      />
    </>
  );
});
