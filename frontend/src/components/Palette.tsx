import { useMemo, useState } from "react";
import { useEditor } from "../store/editor";
import { DRAG_TYPE, useAddAtCenter } from "./Canvas";
import { categoryColor } from "./EtlNodeView";
import { matches } from "./QuickAdd";
import { language, t } from "../i18n";

export function Palette() {
  const palette = useEditor((s) => s.palette);
  const errors = useEditor((s) => s.pluginErrors);
  const [query, setQuery] = useState("");
  const addAtCenter = useAddAtCenter();

  const groups = useMemo(() => {
    const out = new Map<string, typeof palette>();
    for (const t of palette.filter((p) => !p.hidden && matches(p, query))) out.set(t.category, [...(out.get(t.category) ?? []), t]);
    return [...out.entries()].sort(([a], [b]) => a.localeCompare(b, language()));
  }, [palette, query]);

  return (
    <aside className="palette" aria-label={t("Palette de modifiers")}>
      <input type="search" placeholder={t("Rechercher un modificateur…")} value={query} onChange={(e) => setQuery(e.target.value)} aria-label={t("Rechercher un modificateur")} />
      <div className="palette-list">
        {groups.map(([category, items]) => (
          // repliées à l'ouverture ; dépliées pendant une recherche, pour voir les modificateurs trouvés
          <details key={category} open={query ? true : undefined} className="palette-group">
            <summary style={{ borderLeftColor: categoryColor(category) }}>{category}</summary>
            {items.map((t) => (
              <div
                key={t.name}
                className="palette-item"
                draggable
                role="button"
                tabIndex={0}
                title={t.description}
                data-testid={`palette-${t.name}`}
                onDragStart={(e) => {
                  e.dataTransfer.setData(DRAG_TYPE, t.name);
                  e.dataTransfer.effectAllowed = "copy";
                }}
                onDoubleClick={() => addAtCenter(t.name)}
                onKeyDown={(e) => e.key === "Enter" && addAtCenter(t.name)}
              >
                {t.label}
              </div>
            ))}
          </details>
        ))}
        {groups.length === 0 && <p className="muted">{t("Aucun modificateur ne correspond.")}</p>}
        {Object.entries(errors).map(([source, message]) => (
          <p key={source} className="plugin-error" title={message}>
            {t("Plugin ignoré : {{source}}", { source })}
          </p>
        ))}
      </div>
    </aside>
  );
}
