import { useRef, type KeyboardEvent, type PointerEvent } from "react";
import { t } from "../i18n";

/** Poignée à glisser pour régler une taille (flèches du clavier : 24 px).
 *
 * - `edge="top"` : bord supérieur d'un panneau du bas, la hauteur grandit vers le haut ;
 * - `edge="left"` : bord gauche d'un panneau de droite, la largeur grandit vers la gauche.
 *
 * `set(taille, false)` pendant le glissement, `set(taille)` à la fin (taille enregistrée). */
export function ResizeHandle({ edge, size, set, label }: { edge: "top" | "left"; size: () => number; set: (size: number, persist?: boolean) => void; label: string }) {
  const start = useRef<{ at: number; size: number } | null>(null);
  const position = (e: PointerEvent<HTMLDivElement>) => (edge === "top" ? e.clientY : e.clientX);
  const onDown = (e: PointerEvent<HTMLDivElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    start.current = { at: position(e), size: size() };
  };
  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    if (start.current) set(start.current.size + (start.current.at - position(e)), false);
  };
  const onUp = () => {
    if (!start.current) return;
    start.current = null;
    set(size()); // enregistre la taille choisie
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const grow = edge === "top" ? "ArrowUp" : "ArrowLeft";
    const shrink = edge === "top" ? "ArrowDown" : "ArrowRight";
    const step = e.key === grow ? 24 : e.key === shrink ? -24 : 0;
    if (step) {
      e.preventDefault();
      set(size() + step);
    }
  };
  return (
    <div
      className={`resize-handle ${edge}`}
      role="separator"
      aria-orientation={edge === "top" ? "horizontal" : "vertical"}
      aria-label={label}
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
