import { useEffect, useMemo, useRef, useState } from "react";
import type { ModifierInfo } from "../api/types";
import { isEtl } from "../model/graph";
import { useEditor } from "../store/editor";
import { t } from "../i18n";

export function matches(info: ModifierInfo, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [info.name, info.label, info.category, info.description].some((t) => t.toLowerCase().includes(q));
}

/** Ordre des résultats : nom exact, puis nom qui commence par la saisie, puis le reste (ordre de la palette). */
function rank(info: ModifierInfo, query: string): number {
  const q = query.trim().toLowerCase();
  const name = info.name.toLowerCase();
  if (name === q) return 0;
  if (name.startsWith(q)) return 1;
  return info.label.toLowerCase().startsWith(q) ? 2 : 3;
}

/** Ajout rapide : on tape quelques lettres, Entrée ajoute le premier résultat (relié au nœud sélectionné). */
export function QuickAdd({ initial, at, palette, onClose }: { initial: string; at: { x: number; y: number }; palette: Map<string, ModifierInfo>; onClose: () => void }) {
  const [query, setQuery] = useState(initial);
  const [index, setIndex] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const results = useMemo(
    () =>
      [...palette.values()]
        .filter((t) => !t.hidden && matches(t, query))
        .sort((a, b) => rank(a, query) - rank(b, query))
        .slice(0, 8),
    [palette, query],
  );

  useEffect(() => {
    input.current?.focus();
    input.current?.setSelectionRange(initial.length, initial.length);
  }, [initial]);

  const choose = (info: ModifierInfo) => {
    const s = useEditor.getState();
    const before = s.nodes.filter((n) => n.selected).filter(isEtl);
    // Relié à un nœud sélectionné : on place le nouveau juste à sa droite, sinon sous le curseur
    const anchor = before.length === 1 ? before[0] : null;
    const id = s.addNode(info.name, anchor ? { x: anchor.position.x + 250, y: anchor.position.y } : at);
    if (id && before.length === 1 && info.inputs.length > 0) {
      const from = palette.get(before[0].data.type);
      const out = from?.outputs.find((o) => o !== "Rejected") ?? from?.outputs[0];
      if (out) useEditor.getState().onConnect({ source: before[0].id, sourceHandle: out, target: id, targetHandle: info.inputs[0] });
    }
    onClose();
  };

  return (
    <div className="quick-add" role="dialog" aria-label={t("Ajout rapide d'un modificateur")}>
      <input
        ref={input}
        value={query}
        placeholder={t("Nom du modificateur…")}
        onChange={(e) => {
          setQuery(e.target.value);
          setIndex(0);
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
          else if (e.key === "ArrowDown") setIndex((i) => Math.min(i + 1, results.length - 1));
          else if (e.key === "ArrowUp") setIndex((i) => Math.max(i - 1, 0));
          else if (e.key === "Enter" && results[index]) choose(results[index]);
        }}
        onBlur={() => setTimeout(onClose, 150)}
      />
      <ul>
        {results.map((t, i) => (
          <li key={t.name} className={i === index ? "active" : ""} onMouseDown={() => choose(t)}>
            <strong>{t.label}</strong>
            <small>{t.category}</small>
          </li>
        ))}
        {results.length === 0 && <li className="muted">{t("Aucun modifier")}</li>}
      </ul>
    </div>
  );
}
