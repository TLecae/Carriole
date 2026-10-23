import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  SelectionMode,
  useReactFlow,
  type Connection,
  type Edge,
  type Node,
} from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { isEtl, type AppEdge, type AppNode } from "../model/graph";
import { useEditor } from "../store/editor";
import { useInspector } from "../store/inspector";
import { useUi } from "../store/ui";
import { EtlNodeView } from "./EtlNodeView";
import { BookmarkView, EtlEdgeView, NoteNodeView } from "./OtherNodes";
import { QuickAdd } from "./QuickAdd";
import { t } from "../i18n";

export const DRAG_TYPE = "application/x-etl-modifier";

const nodeTypes = { etl: EtlNodeView, note: NoteNodeView, bookmark: BookmarkView };
const edgeTypes = { etl: EtlEdgeView };

/** Ajoute un modificateur au centre de la zone visible (clic dans la palette). */
export function useAddAtCenter() {
  const { screenToFlowPosition } = useReactFlow();
  return useCallback(
    (type: string) => {
      const box = document.querySelector(".react-flow")?.getBoundingClientRect();
      const center = box ? { x: box.left + box.width / 2, y: box.top + box.height / 2 } : { x: 400, y: 300 };
      return useEditor.getState().addNode(type, screenToFlowPosition(center));
    },
    [screenToFlowPosition],
  );
}

export function Canvas() {
  const { screenToFlowPosition } = useReactFlow();
  const nodes = useEditor((s) => s.nodes);
  const edges = useEditor((s) => s.edges);
  const palette = useEditor((s) => s.paletteMap);
  const { onNodesChange, onEdgesChange, onConnect, beginDrag, addNode, addNote } = useEditor.getState();
  const mouse = useRef({ x: 0, y: 0 });
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // Tant que la souris n'a pas bougé, l'ajout rapide vise le centre du canvas
    const r = box.current?.getBoundingClientRect();
    if (r) mouse.current = { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }, []);
  const [quick, setQuick] = useState<{ text: string; at: { x: number; y: number } } | null>(null);

  // Saisie directe sur le canvas : taper un caractère ouvre l'ajout rapide.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.closest("input, textarea, select, [contenteditable=true]") || t.closest(".quick-add"))) return;
      if (e.ctrlKey || e.metaKey || e.altKey || e.key.length !== 1 || !/\S/.test(e.key)) return;
      e.preventDefault();
      setQuick({ text: e.key, at: screenToFlowPosition(mouse.current) });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [screenToFlowPosition]);

  const isValidConnection = useCallback(
    (c: Connection | Edge) => {
      if (!c.source || !c.target || c.source === c.target) return false;
      const target = useEditor.getState().nodes.find((n) => n.id === c.target);
      return !!target && isEtl(target);
    },
    [],
  );

  const miniColor = useMemo(() => (n: Node) => (n.type === "bookmark" ? "#cfe3ff" : n.type === "note" ? "#fff3bf" : "#7a869a"), []);

  return (
    <div
      ref={box}
      className="canvas"
      onMouseMove={(e) => (mouse.current = { x: e.clientX, y: e.clientY })}
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes(DRAG_TYPE)) {
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
        }
      }}
      onDrop={(e) => {
        const type = e.dataTransfer.getData(DRAG_TYPE);
        if (!type) return;
        e.preventDefault();
        addNode(type, screenToFlowPosition({ x: e.clientX, y: e.clientY }));
      }}
    >
      <ReactFlow<AppNode, AppEdge>
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeDragStart={beginDrag}
        onEdgeClick={(_, edge) => {
          const run = useEditor.getState().run;
          if (!run.id) return;
          void useInspector.getState().setTarget({ run: run.id, node: edge.source, port: edge.sourceHandle ?? "output" });
          useUi.getState().show("data");
        }}
        onNodeClick={(e, node) => {
          // chaque clic, même sur un nœud déjà sélectionné : ses données dans l'inspecteur, onglet Données affiché
          if (node.type !== "etl" || e.shiftKey || e.ctrlKey || e.metaKey) return;
          const run = useEditor.getState().run;
          useInspector.getState().showNode(run, node.id);
          if (run.id) useUi.getState().show("data");
        }}
        isValidConnection={isValidConnection}
        onDoubleClick={(e) => {
          if ((e.target as HTMLElement).classList.contains("react-flow__pane")) setQuick({ text: "", at: screenToFlowPosition({ x: e.clientX, y: e.clientY }) });
        }}
        deleteKeyCode={["Delete", "Backspace"]}
        multiSelectionKeyCode={["Shift", "Control"]}
        selectionOnDrag
        panOnDrag={[1, 2]}
        selectionMode={SelectionMode.Partial}
        zoomOnDoubleClick={false}
        minZoom={0.1}
        maxZoom={2}
        snapToGrid
        snapGrid={[10, 10]}
        fitView
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={20} />
        <MiniMap pannable zoomable nodeColor={miniColor} />
        <Controls showInteractive={false} />
      </ReactFlow>
      {nodes.length === 0 && (
        <div className="empty-hint">
          {t("Glissez un modificateur depuis la palette, ou tapez son nom directement ici.")}
          <br />
          <button type="button" className="link" onClick={() => addNote(screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 2 }))}>
            {t("Ajouter une annotation")}
          </button>
        </div>
      )}
      {quick && <QuickAdd initial={quick.text} at={quick.at} palette={palette} onClose={() => setQuick(null)} />}
    </div>
  );
}
