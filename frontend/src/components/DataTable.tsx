import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useRef, useState } from "react";
import type { ColumnFilter, FilterOp, PortInfo } from "../api/types";
import { PAGE_SIZE, useInspector } from "../store/inspector";
import { formatNumber, t } from "../i18n";
import { Spinner } from "./Spinner";

const ROW_H = 26;
const COL_W = 150;
const FID_W = 70;

const OPS: { op: FilterOp; text?: boolean; numeric?: boolean }[] = [
  { op: "contains", text: true },
  { op: "equals" },
  { op: "not_equals" },
  { op: "starts", text: true },
  { op: "ends", text: true },
  { op: "gt", numeric: true },
  { op: "gte", numeric: true },
  { op: "lt", numeric: true },
  { op: "lte", numeric: true },
  { op: "between", numeric: true },
  { op: "in" },
  { op: "null" },
  { op: "not_null" },
];

const opLabel = (op: FilterOp): string =>
  (
    ({
    contains: t("contient"),
    equals: "=",
    not_equals: "≠",
    starts: t("commence par"),
    ends: t("finit par"),
    gt: ">",
    gte: "≥",
    lt: "<",
    lte: "≤",
    between: t("entre"),
    in: t("dans la liste"),
    null: t("est nul"),
    not_null: t("n'est pas nul"),
  }) as Partial<Record<FilterOp, string>>
  )[op] ?? op;

const fmt = (v: unknown): string => (v === null || v === undefined ? "" : typeof v === "number" ? formatNumber(v, 10) : String(v));
/** Valeur copiée : brute (un nombre sans séparateur de milliers se recolle dans un tableur). */
const raw = (v: unknown): string => (v === null || v === undefined ? "" : String(v));

const MIN_W = 50;
const MAX_W = 1200;

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // presse-papiers refusé (page sans focus…) : l'ancienne méthode, par une zone de texte temporaire
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.append(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  }
}

/** Largeur qui laisse lire le nom de la colonne et ses valeurs chargées (bornée). */
function fitWidth(name: string, values: unknown[]): number {
  const ctx = document.createElement("canvas").getContext("2d");
  if (!ctx) return COL_W;
  const family = getComputedStyle(document.body).fontFamily;
  ctx.font = `600 13px ${family}`;
  let w = ctx.measureText(name).width + 28;
  ctx.font = `13px ${family}`;
  for (const v of values) w = Math.max(w, ctx.measureText(fmt(v)).width + 16);
  return Math.min(MAX_W, Math.max(MIN_W, Math.ceil(w)));
}

type Cell = { row: number; col: number }; // ligne (rang dans la table) et colonne (0 : fid)
type Menu = { x: number; y: number; column: string; cell: Cell | null };

const colOf = (e: React.MouseEvent) => Number((e.target as HTMLElement).closest<HTMLElement>("[data-col]")?.dataset.col ?? NaN);
const isActive = (a: Cell | null, row: number, col: number) => !!a && a.row === row && a.col === col;

export function DataTable({ info }: { info: PortInfo }) {
  const { columns, pages, filtered, selectedFid, scrollTo, locating, query, stats, showStats, error } = useInspector();
  const { loadPage, select, toggleSort, setFilter } = useInspector.getState();
  const parent = useRef<HTMLDivElement>(null);
  const virt = useVirtualizer({ count: filtered, getScrollElement: () => parent.current, estimateSize: () => ROW_H, overscan: 25 });
  const items = virt.getVirtualItems();
  const [widths, setWidths] = useState<Record<string, number>>({}); // largeurs choisies à la souris
  const [active, setActive] = useState<Cell | null>(null); // cellule choisie : Ctrl+C la copie
  const [menu, setMenu] = useState<Menu | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  // Pages manquantes : chargées à la demande selon les lignes visibles.
  const first = items[0]?.index ?? 0;
  const last = items[items.length - 1]?.index ?? 0;
  useEffect(() => {
    for (let p = Math.floor(first / PAGE_SIZE); p <= Math.floor(last / PAGE_SIZE); p++) void loadPage(p);
  }, [first, last, loadPage, columns, filtered]);

  // La carte a sélectionné une entité : on fait défiler la table jusqu'à sa ligne.
  useEffect(() => {
    if (scrollTo) virt.scrollToIndex(scrollTo.index, { align: "center" });
  }, [scrollTo, virt]);

  useEffect(() => {
    parent.current?.scrollTo({ top: 0 });
    setActive(null);
  }, [query]);

  // menu contextuel : fermé par un clic ailleurs, Échap, ou en quittant la fenêtre
  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("pointerdown", close);
    window.addEventListener("keydown", onKey);
    window.addEventListener("blur", close);
    return () => {
      window.removeEventListener("pointerdown", close);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("blur", close);
    };
  }, [menu]);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(null), 1500);
    return () => clearTimeout(timer);
  }, [copied]);

  const rowAt = (index: number) => pages[Math.floor(index / PAGE_SIZE)]?.[index % PAGE_SIZE];
  const copy = async (text: string, done: string) => {
    setMenu(null);
    if (await copyText(text)) setCopied(done);
  };
  const copyCell = (cell: Cell) => {
    const row = rowAt(cell.row);
    if (row) void copy(raw(row[cell.col]), t("Valeur copiée"));
  };
  const copyRow = (index: number) => {
    const row = rowAt(index);
    if (row) void copy(row.map(raw).join("\t"), t("Ligne copiée"));
  };

  const meta = columns.map((name) => ({ name, info: info.columns.find((c) => c.name === name) }));
  const widthOf = (name: string) => widths[name] ?? COL_W;
  const width = FID_W + meta.slice(1).reduce((sum, { name }) => sum + widthOf(name), 0);

  const startResize = (name: string, e: React.PointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const x0 = e.clientX;
    const w0 = widthOf(name);
    const move = (ev: PointerEvent) => setWidths((w) => ({ ...w, [name]: Math.min(MAX_W, Math.max(MIN_W, w0 + ev.clientX - x0)) }));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      document.body.classList.remove("col-resizing");
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    document.body.classList.add("col-resizing");
  };
  const fit = (name: string) => {
    const col = columns.indexOf(name);
    const values = Object.values(pages).flatMap((rows) => rows.map((r) => r[col]));
    setWidths((w) => ({ ...w, [name]: fitWidth(name === "geometry" ? t("géométrie") : name, values) }));
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "c" && active && !window.getSelection()?.toString()) {
      e.preventDefault();
      copyCell(active);
    }
  };

  return (
    <div className="table-wrap" role="region" aria-label={t("Table de données")} data-own-keys>
      <div className="table-scroll" ref={parent} tabIndex={0} onKeyDown={onKeyDown}>
        <div style={{ width, minWidth: "100%" }}>
          <div className="table-head" style={{ width }}>
            <div className="cell fid" style={{ width: FID_W }}>
              fid
            </div>
            {meta.slice(1).map(({ name, info: col }) => {
              const sortIdx = query.sort.findIndex((k) => k.column === name);
              const sortKey = query.sort[sortIdx];
              const isGeom = name === "geometry";
              const stat = stats?.columns[name];
              return (
                <div
                  className="cell head"
                  key={name}
                  style={{ width: widthOf(name) }}
                  title={col ? `${name} (${col.type})` : name}
                  onContextMenu={(e) => {
                    e.preventDefault();
                    setMenu({ x: e.clientX, y: e.clientY, column: name, cell: null });
                  }}
                >
                  <button type="button" className="sort" disabled={isGeom} onClick={(e) => toggleSort(name, e.shiftKey)} aria-label={t("Trier par {{name}}", { name })}>
                    <span className="col-name">{isGeom ? t("géométrie") : name}</span>
                    {sortKey && (
                      <span className="sort-mark">
                        {sortKey.desc ? "▼" : "▲"}
                        {query.sort.length > 1 ? sortIdx + 1 : ""}
                      </span>
                    )}
                  </button>
                  <small className="col-type">{isGeom ? t("type") : col?.type}</small>
                  {showStats && !isGeom && stat && (
                    <small className="col-stats">
                      {stat.min !== null && `${fmt(stat.min)} → ${fmt(stat.max)}`}
                      <br />
                      {t("{{distinct}} distinctes · {{nulls}} nulles", { distinct: stat.distinct, nulls: stat.nulls })}
                    </small>
                  )}
                  {showStats && isGeom && stats?.geometry && (
                    <small className="col-stats">
                      {stats.geometry.types.join(", ")}
                      <br />
                      {t("{{crs}} · {{invalid}} invalide(s)", { crs: stats.geometry.crs, invalid: stats.geometry.invalid ?? 0 })}
                    </small>
                  )}
                  {!isGeom && col && <FilterCell column={name} category={col.category} current={query.filters.find((f) => f.column === name)} onChange={(f) => setFilter(name, f)} />}
                  <span
                    className="col-resize"
                    role="separator"
                    aria-orientation="vertical"
                    aria-label={t("Largeur de la colonne {{name}}", { name })}
                    title={t("Glisser pour élargir ; double-clic : ajuster au contenu")}
                    onPointerDown={(e) => startResize(name, e)}
                    onDoubleClick={() => fit(name)}
                  />
                </div>
              );
            })}
          </div>
          <div className="table-body" style={{ height: virt.getTotalSize(), width }}>
            {items.map((item) => {
              const row = rowAt(item.index);
              const fid = row ? (row[0] as number) : null;
              return (
                <div
                  key={item.key}
                  className={`row${fid !== null && fid === selectedFid ? " selected" : ""}${row ? "" : " loading"}`}
                  style={{ transform: `translateY(${item.start}px)`, height: ROW_H, width }}
                  onClick={(e) => {
                    if (window.getSelection()?.toString()) return; // on sélectionnait du texte, pas l'entité
                    const col = colOf(e);
                    if (Number.isInteger(col)) setActive({ row: item.index, col });
                    if (fid !== null) void select(fid === selectedFid ? null : fid, "table");
                  }}
                  onContextMenu={(e) => {
                    const col = colOf(e);
                    if (!row || !Number.isInteger(col)) return;
                    e.preventDefault();
                    const cell = { row: item.index, col };
                    setActive(cell);
                    setMenu({ x: e.clientX, y: e.clientY, column: columns[col], cell });
                  }}
                  data-testid="data-row"
                >
                  <div className={`cell fid${isActive(active, item.index, 0) ? " active" : ""}`} style={{ width: FID_W }} data-col={0}>
                    {fid ?? ""}
                  </div>
                  {row?.slice(1).map((v, i) => (
                    <div
                      className={`cell${typeof v === "number" ? " num" : ""}${isActive(active, item.index, i + 1) ? " active" : ""}`}
                      key={i}
                      style={{ width: widthOf(columns[i + 1]) }}
                      title={fmt(v)}
                      data-col={i + 1}
                    >
                      {fmt(v)}
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        </div>
      </div>
      {filtered === 0 && !error && <p className="muted pad">{t("Aucune entité ne correspond aux filtres.")}</p>}
      {menu && (
        <div className="menu-panel table-menu" role="menu" style={{ left: menu.x, top: menu.y }} onPointerDown={(e) => e.stopPropagation()}>
          {menu.cell && (
            <>
              <button type="button" role="menuitem" className="menu-item" onClick={() => menu.cell && copyCell(menu.cell)}>
                <span />
                {t("Copier la valeur")}
                <kbd>Ctrl+C</kbd>
              </button>
              <button type="button" role="menuitem" className="menu-item" onClick={() => menu.cell && copyRow(menu.cell.row)}>
                <span />
                {t("Copier la ligne")}
                <span />
              </button>
            </>
          )}
          <button type="button" role="menuitem" className="menu-item" onClick={() => void copy(menu.column, t("Nom de colonne copié"))}>
            <span />
            {t("Copier le nom de la colonne")}
            <span />
          </button>
          {!menu.cell && (
            <button
              type="button"
              role="menuitem"
              className="menu-item"
              onClick={() => {
                fit(menu.column);
                setMenu(null);
              }}
            >
              <span />
              {t("Ajuster la largeur au contenu")}
              <span />
            </button>
          )}
        </div>
      )}
      {copied && (
        <div className="table-copied" role="status">
          {copied}
        </div>
      )}
      {locating && (
        <div className="float-status" role="status" data-testid="table-locating">
          <Spinner size={12} /> {t("Recherche de l'entité dans la table…")}
        </div>
      )}
    </div>
  );
}

function FilterCell({ column, category, current, onChange }: { column: string; category: string; current?: ColumnFilter; onChange: (f: ColumnFilter | null) => void }) {
  const ops = OPS.filter((o) => (category === "text" ? !o.numeric || ["gt", "gte", "lt", "lte", "between"].includes(o.op) : !o.text) );
  const [op, setOp] = useState<FilterOp>(current?.op ?? (category === "text" ? "contains" : "equals"));
  const [a, setA] = useState(String(current?.value ?? current?.values?.join(", ") ?? ""));
  const [b, setB] = useState(String(current?.value2 ?? ""));

  const apply = (nextOp: FilterOp, va: string, vb: string) => {
    if (nextOp === "null" || nextOp === "not_null") return onChange({ column, op: nextOp });
    if (va.trim() === "") return onChange(null);
    if (nextOp === "in") return onChange({ column, op: nextOp, values: va.split(",").map((s) => s.trim()).filter(Boolean) });
    if (nextOp === "between") return onChange({ column, op: nextOp, value: va, value2: vb });
    onChange({ column, op: nextOp, value: va });
  };
  const noValue = op === "null" || op === "not_null";
  return (
    <div className="filter">
      <select
        aria-label={t("Opérateur du filtre {{column}}", { column })}
        value={op}
        onChange={(e) => {
          setOp(e.target.value as FilterOp);
          apply(e.target.value as FilterOp, a, b);
        }}
      >
        {ops.map((o) => (
          <option key={o.op} value={o.op}>
            {opLabel(o.op)}
          </option>
        ))}
      </select>
      {!noValue && (
        <input
          type="text"
          aria-label={t("Filtre {{column}}", { column })}
          value={a}
          placeholder={t("filtre…")}
          onChange={(e) => setA(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && apply(op, a, b)}
          onBlur={() => apply(op, a, b)}
        />
      )}
      {op === "between" && (
        <input type="text" aria-label={t("Filtre {{column}} (fin)", { column })} value={b} placeholder={t("et…")} onChange={(e) => setB(e.target.value)} onKeyDown={(e) => e.key === "Enter" && apply(op, a, b)} onBlur={() => apply(op, a, b)} />
      )}
    </div>
  );
}
