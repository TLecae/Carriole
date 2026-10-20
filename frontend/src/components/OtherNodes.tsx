import { BaseEdge, getBezierPath, type EdgeProps, type NodeProps } from "@xyflow/react";
import { memo } from "react";
import { useEditor } from "../store/editor";
import type { AppEdge, BookmarkNode, NoteNode } from "../model/graph";
import { t } from "../i18n";

export const NoteNodeView = memo(function NoteNodeView({ id, data, selected }: NodeProps<NoteNode>) {
  const update = useEditor((s) => s.updateNodeData);
  return (
    <div className={`note-node${selected ? " selected" : ""}`}>
      <textarea
        className="nodrag"
        aria-label={t("Texte de l'annotation")}
        value={data.text}
        onChange={(e) => update(id, { text: e.target.value }, "text")}
      />
    </div>
  );
});

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
        className={`etl-edge${disabled ? " disabled" : ""}${rejected ? " rejected" : ""}${selected ? " selected" : ""}`}
      />
    </>
  );
});
