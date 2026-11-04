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
import { isEtl, NODE_WIDTH, type AppEdge, type AppNode } from "../model/graph";
import { useEditor } from "../store/editor";
import { useInspector } from "../store/inspector";
import { useUi, type CanvasPick } from "../store/ui";
import { EtlNodeView } from "./EtlNodeView";
import { Icon } from "./Icon";
import { BookmarkView, EtlEdgeView, NoteNodeView } from "./OtherNodes";
import { QuickAdd } from "./QuickAdd";
import { t } from "../i18n";
import { useTheme } from "../theme";

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

type Point = { x: number; y: number };

/** Points le long de chaque liaison affichée (coordonnées du flux), lus dans le SVG de React Flow. */
function edgeSamples(root: HTMLElement | null, edges: AppEdge[]): Map<string, Point[]> {
  const out = new Map<string, Point[]>();
  for (const e of edges) {
    const path = root?.querySelector<SVGPathElement>(`.react-flow__edge[data-id="${CSS.escape(e.id)}"] .react-flow__edge-path`);
    if (!path?.getTotalLength) continue;
    const length = path.getTotalLength();
    const steps = Math.min(200, Math.max(2, Math.ceil(length / 8)));
    const points: Point[] = [];
    for (let i = 0; i <= steps; i++) {
      const p = path.getPointAtLength((length * i) / steps);
      points.push({ x: p.x, y: p.y });
    }
    out.set(e.id, points);
  }
  return out;
}

/** Liaison qui passe sous le rectangle du nœud déplacé : la première trouvée. */
function edgeUnder(samples: Map<string, Point[]>, x: number, y: number, w: number, h: number): string | null {
  for (const [id, points] of samples) {
    if (points.some((p) => p.x >= x && p.x <= x + w && p.y >= y && p.y <= y + h)) return id;
  }
  return null;
}

function pickHint(pick: CanvasPick): string {
  if (pick.kind === "note") return t("Cliquez sur le modificateur auquel lier l'annotation (Échap pour annuler)");
  return pick.side === "out"
    ? t("Cliquez sur le modificateur à brancher à la sortie « {{port}} » de {{node}} (Échap pour annuler)", { port: pick.port, node: pick.node })
    : t("Cliquez sur le modificateur à brancher à l'entrée « {{port}} » de {{node}} (Échap pour annuler)", { port: pick.port, node: pick.node });
}

export function Canvas() {
  const { screenToFlowPosition } = useReactFlow();
  const nodes = useEditor((s) => s.nodes);
  const edges = useEditor((s) => s.edges);
  const palette = useEditor((s) => s.paletteMap);
  const { onNodesChange, onEdgesChange, onConnect, beginDrag, addNode, addNote } = useEditor.getState();
  const pick = useUi((s) => s.pick);
  const miniMap = useUi((s) => s.miniMap);
  const drop = useRef<{ node: string; samples: Map<string, Point[]> } | null>(null);
  const mouse = useRef({ x: 0, y: 0 });
  useEffect(() => {
    // choix en cours dont le nœud a disparu (supprimé, autre flux ouvert) : abandonné
    const from = pick ? (pick.kind === "note" ? pick.note : pick.node) : null;
    if (from && !nodes.some((n) => n.id === from)) useUi.getState().setPick(null);
  }, [nodes, pick]);
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
      if (e.key === "Escape" && useUi.getState().pick) {
        useUi.getState().setPick(null);
        return;
      }
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

  const theme = useTheme();
  const miniColor = useMemo(() => (n: Node) => (n.type === "bookmark" ? "#cfe3ff" : n.type === "note" ? "#fff3bf" : "#7a869a"), []);

  /** Un nœud seul, sans liaison, avec une entrée et une sortie, peut être lâché sur une liaison pour s'y intercaler. */
  const startDrag = (node: AppNode, dragged: AppNode[]) => {
    beginDrag();
    drop.current = null;
    const { edges, analysis, paletteMap } = useEditor.getState();
    if (dragged.length !== 1 || !isEtl(node) || edges.some((e) => e.source === node.id || e.target === node.id)) return;
    const ports = analysis?.ports?.[node.id] ?? paletteMap.get(node.data.type);
    if (!ports?.inputs.length || !ports.outputs.length) return;
    drop.current = { node: node.id, samples: edgeSamples(box.current, edges) };
  };

  /** Clic sur un nœud pendant un choix (branchement, lien d'annotation) : le termine. */
  const finishPick = (current: CanvasPick, node: AppNode) => {
    const editor = useEditor.getState();
    if (current.kind === "note") editor.linkNote(current.note, isEtl(node) ? node.id : null);
    else editor.connectTo(current, node.id);
    useUi.getState().setPick(null);
  };

  return (
    <div
      ref={box}
      className={`canvas${pick ? " picking" : ""}`}
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
        colorMode={theme}
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeDragStart={(_, node, dragged) => startDrag(node, dragged)}
        onNodeDrag={(_, node) => {
          if (!drop.current) return;
          const hit = edgeUnder(drop.current.samples, node.position.x, node.position.y, node.measured?.width ?? NODE_WIDTH, node.measured?.height ?? 60);
          if (hit !== useUi.getState().dropEdge) useUi.getState().setDropEdge(hit);
        }}
        onNodeDragStop={() => {
          const edge = useUi.getState().dropEdge;
          if (drop.current && edge) useEditor.getState().insertOnEdge(drop.current.node, edge);
          drop.current = null;
          useUi.getState().setDropEdge(null);
        }}
        connectOnClick={false}
        onPaneClick={() => useUi.getState().setPick(null)}
        onEdgeClick={(_, edge) => {
          const run = useEditor.getState().run;
          if (!run.id) return;
          void useInspector.getState().setTarget({ run: run.id, node: edge.source, port: edge.sourceHandle ?? "output" });
          useUi.getState().show("data");
        }}
        onNodeClick={(e, node) => {
          const current = useUi.getState().pick;
          if (current) {
            finishPick(current, node);
            return;
          }
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
        {miniMap && <MiniMap pannable zoomable nodeColor={miniColor} />}
        <Controls showInteractive={false} />
      </ReactFlow>
      <button
        type="button"
        className={`minimap-toggle${miniMap ? " open" : ""}`}
        title={miniMap ? t("Replier la carte de situation") : t("Afficher la carte de situation")}
        aria-label={miniMap ? t("Replier la carte de situation") : t("Afficher la carte de situation")}
        aria-pressed={miniMap}
        data-testid="minimap-toggle"
        onClick={() => useUi.getState().toggleMiniMap()}
      >
        <Icon name={miniMap ? "chevron" : "map"} size={14} />
      </button>
      {pick && (
        <div className="pick-hint" role="status">
          {pickHint(pick)}
        </div>
      )}
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
