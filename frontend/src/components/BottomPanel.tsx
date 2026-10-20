import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { useEditor } from "../store/editor";
import { useUi } from "../store/ui";
import { DataPanel } from "./DataPanel";
import { formatTime, t } from "../i18n";

const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"];
const RANK: Record<string, number> = { DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40 };

/** Poignée au bord supérieur du panneau : glisser pour régler la hauteur (flèches du clavier : 24 px). */
function ResizeHandle() {
  const start = useRef<{ y: number; height: number } | null>(null);
  const { setBottomHeight } = useUi.getState();
  const onDown = (e: PointerEvent<HTMLDivElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    start.current = { y: e.clientY, height: useUi.getState().bottomHeight };
  };
  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    if (start.current) setBottomHeight(start.current.height + (start.current.y - e.clientY), false);
  };
  const onUp = () => {
    if (!start.current) return;
    start.current = null;
    setBottomHeight(useUi.getState().bottomHeight); // enregistre la hauteur choisie
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.key === "ArrowUp" ? 24 : e.key === "ArrowDown" ? -24 : 0;
    if (step) {
      e.preventDefault();
      setBottomHeight(useUi.getState().bottomHeight + step);
    }
  };
  return (
    <div
      className="resize-handle"
      role="separator"
      aria-orientation="horizontal"
      aria-label={t("Redimensionner le panneau (glisser, ou flèches haut et bas)")}
      title={t("Glisser pour redimensionner")}
      tabIndex={0}
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={onUp}
      onPointerCancel={onUp}
      onKeyDown={onKey}
    />
  );
}

export function BottomPanel() {
  const { tab, fullscreen } = useUi();
  const { show: setTab, setFullscreen } = useUi.getState();
  const analysis = useEditor((s) => s.analysis);
  const issues = analysis?.issues ?? [];
  const errors = issues.filter((i) => i.level === "error").length;
  const run = useEditor((s) => s.run);

  // Échap referme le plein écran ; la fenêtre redimensionnée ne doit pas laisser le panneau plus grand que l'écran.
  useEffect(() => {
    if (!fullscreen) return;
    const onKey = (e: globalThis.KeyboardEvent) => e.key === "Escape" && setFullscreen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreen, setFullscreen]);
  useEffect(() => {
    const onResize = () => useUi.getState().setBottomHeight(useUi.getState().bottomHeight, false);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  return (
    <section className={`bottom-panel${fullscreen ? " fullscreen" : ""}`} aria-label={t("Journal et avertissements")}>
      {!fullscreen && <ResizeHandle />}
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "log"} onClick={() => setTab("log")}>
          {t("Journal")}
        </button>
        <button role="tab" aria-selected={tab === "issues"} onClick={() => setTab("issues")}>
          {t("Avertissements")} {issues.length > 0 && <span className={`count ${errors ? "error" : "warning"}`}>{issues.length}</span>}
        </button>
        <button role="tab" aria-selected={tab === "data"} onClick={() => setTab("data")}>
          {t("Données")}
        </button>
        <span className="tab-status">{run.id ? t("Run {{id}} : {{status}}", { id: run.id.slice(0, 8), status: run.status }) : t("Aucun run")}</span>
        <button
          type="button"
          className="expand"
          onClick={() => setFullscreen(!fullscreen)}
          aria-pressed={fullscreen}
          title={fullscreen ? t("Revenir à l'éditeur (Échap)") : t("Ouvrir en pleine page")}
        >
          {fullscreen ? t("✕ Fermer le plein écran") : t("⤢ Pleine page")}
        </button>
      </div>
      {tab === "log" ? <Log /> : tab === "issues" ? <Issues /> : <DataPanel />}
    </section>
  );
}

function Log() {
  const logs = useEditor((s) => s.run.logs);
  const runError = useEditor((s) => s.run.error);
  const select = useEditor.getState().select;
  const [node, setNode] = useState("");
  const [level, setLevel] = useState("INFO");
  const nodes = useMemo(() => [...new Set(logs.map((l) => l.node).filter(Boolean))] as string[], [logs]);
  const shown = logs.filter((l) => (!node || l.node === node) && (RANK[l.level ?? "INFO"] ?? 20) >= RANK[level]);
  return (
    <div className="log">
      <div className="log-filters">
        <label>
          {t("Nœud")}{" "}
          <select value={node} onChange={(e) => setNode(e.target.value)} aria-label={t("Filtrer par nœud")}>
            <option value="">{t("tous")}</option>
            {nodes.map((n) => (
              <option key={n}>{n}</option>
            ))}
          </select>
        </label>
        <label>
          {t("Niveau")}{" "}
          <select value={level} onChange={(e) => setLevel(e.target.value)} aria-label={t("Filtrer par niveau")}>
            {LEVELS.map((l) => (
              <option key={l}>{l}</option>
            ))}
          </select>
        </label>
      </div>
      <div className="log-lines" role="log">
        {runError && <div className="log-line ERROR">{t("Run : {{error}}", { error: runError })}</div>}
        {shown.map((l, i) => (
          <button key={i} type="button" className={`log-line ${l.level}`} onClick={() => l.node && select([l.node])} title={t("Sélectionner le nœud")}>
            <time>{l.ts ? formatTime(l.ts) : ""}</time>
            <b>{l.node}</b> {l.message}
          </button>
        ))}
        {shown.length === 0 && !runError && <p className="muted">{t("Aucun message.")}</p>}
      </div>
    </div>
  );
}

function Issues() {
  const analysis = useEditor((s) => s.analysis); // pas de `?? []` dans le sélecteur : nouvelle référence à chaque rendu
  const issues = analysis?.issues ?? [];
  const select = useEditor.getState().select;
  if (!issues.length) return <p className="muted pad">{t("Aucun avertissement.")}</p>;
  return (
    <div className="log-lines" role="list">
      {issues.map((i, k) => (
        <button key={k} type="button" role="listitem" className={`log-line ${i.level === "error" ? "ERROR" : "WARNING"}`} onClick={() => i.node && select([i.node])}>
          <b>{i.node ?? t("flux")}</b> {i.message}
        </button>
      ))}
    </div>
  );
}
