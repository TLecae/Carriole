import { Suspense, lazy, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiError, api } from "../api/client";
import type { JsonSchema, PgTableInfo } from "../api/types";
import { defaultValue, fieldMatches, labelOf, resolveSchema, textMatches, typeOf, widgetOf } from "../model/schema";
import { pickPath } from "../platform";
import { resolvePreview, type VariableHint } from "../model/variables";
import { kindLabel } from "../model/databases";
import { useConnections } from "../store/connections";
import { useUi } from "../store/ui";
import { formatCount, t } from "../i18n";
import { Spinner } from "./Spinner";

export interface FormCtx {
  defs: Record<string, JsonSchema>;
  attributes: string[]; // attributs disponibles en amont du nœud (toutes entrées)
  inputAttributes: Record<string, string[]>; // ceux de chaque entrée : un champ qui désigne la sienne (`x-etl-input`)
  parameters: string[]; // paramètres publiés du flux
  variables: VariableHint[]; // tout ce qui peut s'écrire entre ${ et }
}

/** Attributs proposés par un champ : ceux de son entrée s'il en désigne une, sinon ceux de toutes les entrées. */
const attributesFor = (s: JsonSchema, ctx: FormCtx): string[] => {
  const port = s["x-etl-input"];
  return port ? (ctx.inputAttributes[port] ?? []) : ctx.attributes;
};

// CodeMirror est chargé à la demande : seuls les nœuds qui ont un champ de code le paient.
const CodeEditor = lazy(() => import("./CodeEditor"));

const CRS_PRESETS = ["EPSG:2154", "EPSG:4326", "EPSG:3857", "EPSG:3945", "EPSG:3946", "EPSG:4171", "EPSG:5698"];

interface EditorProps {
  label?: string;
  schema: JsonSchema;
  value: unknown;
  onChange: (v: unknown) => void;
  ctx: FormCtx;
  compact?: boolean;
}

const SEARCH_FROM = 7; // champs à partir desquels le formulaire propose une recherche
const FILTER_FROM = 9; // choix à partir desquels une liste à cocher propose un filtre

/** Formulaire généré depuis le JSON Schema Pydantic des paramètres d'un modificateur. */
export function SchemaForm(props: { schema: JsonSchema; value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void; ctx: FormCtx }) {
  const [query, setQuery] = useState("");
  const root = resolveSchema(props.schema, props.ctx.defs);
  const entries = Object.entries(root.properties ?? {});
  if (!entries.length) return <p className="muted">{t("Ce modificateur n'a pas de paramètre.")}</p>;
  // les champs écartés par la recherche sont masqués, pas retirés : un éditeur de code garde son état
  const shown = new Set(entries.filter(([key, raw]) => fieldMatches(key, raw, props.ctx.defs, query)).map(([key]) => key));
  return (
    <div className="form">
      {entries.length >= SEARCH_FROM && (
        <input type="search" className="form-search" value={query} placeholder={t("Rechercher un paramètre…")} aria-label={t("Rechercher un paramètre…")} onChange={(e) => setQuery(e.target.value)} />
      )}
      {!shown.size && <p className="muted">{t("Aucun paramètre ne correspond.")}</p>}
      {entries.map(([key, raw]) => {
        const s = resolveSchema(raw, props.ctx.defs);
        const value = props.value[key] ?? defaultValue(raw, props.ctx.defs);
        const set = (v: unknown) => props.onChange({ ...props.value, [key]: v });
        // table d'une base : listes lues sur la connexion choisie dans le paramètre voisin
        const connection = widgetOf(s) === "pg-table" ? String(props.value[String(s["x-etl-connection"] ?? "connection")] ?? "") : null;
        return (
          <Field
            key={key}
            hidden={!shown.has(key)}
            label={labelOf(key, s)}
            hint={s.description}
            inline={typeOf(s) === "boolean"}
            simple={typeOf(s) !== "array" && widgetOf(s) !== "expression" && connection === null}
          >
            {connection !== null ? (
              <PgTableSelect label={labelOf(key, s)} connection={connection} value={String(value ?? "")} write={s["x-etl-access"] === "write"} onChange={set} />
            ) : (
              <Editor label={labelOf(key, s)} schema={raw} value={value} ctx={props.ctx} onChange={set} />
            )}
          </Field>
        );
      })}
    </div>
  );
}

/** Un champ simple est un `<label>` (le clic sur le libellé active le champ) ; un champ composé est un groupe. */
function Field({ label, hint, inline, simple, hidden, children }: { label: string; hint?: string; inline?: boolean; simple: boolean; hidden?: boolean; children: ReactNode }) {
  const body = (
    <>
      <span className="field-label">{label}</span>
      {children}
      {hint && !inline && <span className="field-hint">{hint}</span>}
    </>
  );
  const className = inline ? "field inline" : "field";
  return simple ? (
    <label className={className} title={hint} hidden={hidden}>
      {body}
    </label>
  ) : (
    <div className={className} role="group" aria-label={label} title={hint} hidden={hidden}>
      {body}
    </div>
  );
}

function Editor({ label, schema, value, onChange, ctx, compact }: EditorProps) {
  const s = resolveSchema(schema, ctx.defs);
  const widget = widgetOf(s);
  const kind = typeOf(s);

  if (kind === "array") {
    const item = resolveSchema(s.items, ctx.defs);
    if (widget === "attributes") return <CheckList value={asArray(value) as string[]} options={attributesFor(s, ctx)} free onChange={onChange} label={label} />;
    if (item.enum) return <CheckList value={asArray(value) as string[]} options={item.enum.map(String)} onChange={onChange} label={label} />;
    if (typeOf(item) === "object") return <ObjectTable schema={item} rawItem={s.items!} value={asArray(value)} onChange={onChange} ctx={ctx} />;
    return <StringList item={item} value={asArray(value) as string[]} onChange={onChange} ctx={ctx} label={label} />;
  }
  if (kind === "boolean") return <input type="checkbox" aria-label={label} checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />;
  if (s.enum) {
    return (
      <select aria-label={label} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)}>
        {s.enum.map((o) => (
          <option key={String(o)} value={String(o)}>
            {String(o)}
          </option>
        ))}
      </select>
    );
  }
  if (kind === "integer" || kind === "number") {
    return (
      <input
        type="number"
        aria-label={label}
        step={kind === "integer" ? 1 : "any"}
        value={value === "" || value == null ? "" : Number(value)}
        onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))}
      />
    );
  }
  if (widget === "connection") return <ConnectionSelect value={String(value ?? "")} onChange={onChange} label={label} kinds={s["x-etl-kinds"] as string[] | undefined} />;
  const example = typeof s.examples?.[0] === "string" ? s.examples[0] : undefined;
  return <TextEditor widget={widget} value={String(value ?? "")} onChange={onChange} ctx={ctx} compact={compact} label={label} example={example} access={s["x-etl-access"]} codeLanguage={s["x-etl-language"]} extensions={s["x-etl-extensions"] as string[] | undefined} inputPort={s["x-etl-input"]} />;
}

interface TextEditorProps {
  widget?: string;
  value: string;
  onChange: (v: string) => void;
  ctx: FormCtx;
  compact?: boolean;
  label?: string;
  example?: string; // exemple de saisie : affiché tant que le champ est vide
  access?: "read" | "write";
  codeLanguage?: "python" | "sql"; // champ de code : langage de l'éditeur
  extensions?: string[]; // champ chemin : l'explorateur ne montre que ces fichiers (lecteur d'un format)
  inputPort?: string; // champ attribut : l'entrée dont il vient (modificateur à plusieurs entrées)
}

function TextEditor({ widget, value, onChange, ctx, compact, label, example, access, codeLanguage = "python", extensions, inputPort }: TextEditorProps) {
  const listId = useId();
  // Dialogue natif dans l'application de bureau, sinon explorateur du serveur (monté hors de ce champ : voir PickerHost).
  const browse = async () => {
    const path = await pickPath({ mode: access ?? "read", value, extensions });
    if (path) onChange(path);
  };
  const area = useRef<HTMLTextAreaElement>(null);
  const field = useRef<HTMLInputElement>(null);

  const insert = (text: string) => {
    const el = area.current ?? field.current;
    if (!el) return onChange(value + text);
    const a = el.selectionStart ?? value.length;
    const b = el.selectionEnd ?? a;
    onChange(value.slice(0, a) + text + value.slice(b));
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(a + text.length, a + text.length);
    });
  };

  if (widget === "expression") {
    return (
      <div className="expression">
        <textarea ref={area} aria-label={label} rows={compact ? 2 : 3} spellCheck={false} value={value} onChange={(e) => onChange(e.target.value)} placeholder={example ?? t('"attribut" > 10')} />
        <div className="expression-tools">
          <select value="" onChange={(e) => e.target.value && insert(`"${e.target.value}"`)} title={t("Insérer un attribut")}>
            <option value="">{t("+ attribut")}</option>
            {ctx.attributes.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
          {ctx.parameters.length > 0 && (
            <select value="" onChange={(e) => e.target.value && insert(`\${${e.target.value}}`)} title={t("Insérer un paramètre publié")}>
              <option value="">{t("+ paramètre")}</option>
              {ctx.parameters.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          )}
          <VariablePicker ctx={ctx} onPick={insert} />
        </div>
        <VariablePreview value={value} ctx={ctx} />
      </div>
    );
  }
  if (widget === "code") {
    return (
      <div className="code-field">
        <Suspense fallback={<textarea aria-label={label} rows={8} value={value} onChange={(e) => onChange(e.target.value)} />}>
          <CodeEditor value={value} language={codeLanguage} onChange={onChange} label={label} />
        </Suspense>
        <VariablePreview value={value} ctx={ctx} />
      </div>
    );
  }
  if (widget === "connection") return <ConnectionSelect value={value} onChange={onChange} label={label} />;
  const suggestions = widget === "attribute" ? attributesFor({ "x-etl-input": inputPort }, ctx) : widget === "crs" ? CRS_PRESETS : [];
  const placeholder = example ?? (widget === "path" ? t("/data/sources/fichier.gpkg") : widget === "crs" ? "EPSG:2154" : undefined);
  const input = (
    <input ref={field} type="text" aria-label={label} list={suggestions.length ? listId : undefined} value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
  );
  return (
    <>
      {widget === "path" ? (
        <div className="path-field">
          {input}
          <button type="button" aria-label={t("{{label}} : parcourir", { label: label ?? t("Fichier") })} title={t("Choisir un fichier")} onClick={() => void browse()}>
            {t("Parcourir…")}
          </button>
        </div>
      ) : (
        input
      )}
      {!compact && widget !== "attribute" && widget !== "crs" && <VariablePicker ctx={ctx} onPick={insert} />}
      <VariablePreview value={value} ctx={ctx} />
      {suggestions.length > 0 && (
        <datalist id={listId}>
          {suggestions.map((a) => (
            <option key={a} value={a} />
          ))}
        </datalist>
      )}
    </>
  );
}

/** Insère `${…}` dans le champ : paramètres publiés, variables du flux, sys:, var: posées par un SetVariables. */
function VariablePicker({ ctx, onPick }: { ctx: FormCtx; onPick: (text: string) => void }) {
  if (!ctx.variables.length) return null;
  const groups: Record<VariableHint["group"], string> = {
    param: t("Paramètres publiés"),
    flow: t("Variables du flux"),
    sys: t("Système"),
    global: t("Globales"),
    var: t("Posées par les données"),
    env: t("Environnement"),
  };
  return (
    <select className="var-pick" value="" aria-label={t("Insérer une variable")} title={t("Insérer une variable")} onChange={(e) => e.target.value && onPick("${" + e.target.value + "}")}>
      <option value="">{"${…}"}</option>
      {(Object.keys(groups) as VariableHint["group"][]).map((g) => {
        const items = ctx.variables.filter((h) => h.group === g);
        return items.length ? (
          <optgroup key={g} label={groups[g]}>
            {items.map((h) => (
              <option key={h.token} value={h.token}>
                {h.token}
                {h.value !== null && h.value !== "" ? ` = ${h.value}` : ""}
              </option>
            ))}
          </optgroup>
        ) : null;
      })}
    </select>
  );
}

/** Valeur résolue d'un texte qui contient des variables (ce qui est connu avant le run). */
function VariablePreview({ value, ctx }: { value: string; ctx: FormCtx }) {
  if (!value.includes("${")) return null;
  const preview = resolvePreview(value, ctx.variables);
  if (preview.text === value) return null;
  return (
    <small className="var-preview muted" title={t("Valeur avec les variables connues dans l'éditeur")}>
      → {preview.text}
    </small>
  );
}

/** Choix multiple à cases à cocher. Cocher ajoute à la fin, décocher retire : l'ordre des choix est gardé (il compte
 * pour garder des attributs ou joindre sur plusieurs clés). Un choix absent des options (attribut absent en amont)
 * reste visible ; `free` : un nom peut être saisi (amont sans schéma connu). */
function CheckList({ value, options, onChange, label, free }: { value: string[]; options: string[]; onChange: (v: unknown) => void; label?: string; free?: boolean }) {
  const [filter, setFilter] = useState("");
  const [typed, setTyped] = useState("");
  const known = new Set(options);
  const all = [...options, ...value.filter((v) => !known.has(v))];
  const visible = all.filter((o) => textMatches(filter, o));
  const toggle = (o: string, on: boolean) => onChange(on ? [...value, o] : value.filter((v) => v !== o));
  const add = () => {
    const name = typed.trim();
    if (name && !value.includes(name)) onChange([...value, name]);
    setTyped("");
  };
  const name = label ?? t("Attributs");
  return (
    <div className="check-list">
      {all.length >= FILTER_FROM && <input type="search" value={filter} placeholder={t("Filtrer…")} aria-label={t("{{label}} : filtrer", { label: name })} onChange={(e) => setFilter(e.target.value)} />}
      {all.length > 0 && (
        <div className="check-actions">
          <button type="button" className="link" onClick={() => onChange([...value, ...visible.filter((o) => !value.includes(o))])}>
            {t("tous")}
          </button>
          <button type="button" className="link" onClick={() => onChange(value.filter((v) => !visible.includes(v)))}>
            {t("aucun")}
          </button>
          {value.length > 0 && <span className="muted">{t("{{n}} choisi(s)", { n: value.length })}</span>}
        </div>
      )}
      {all.length > 0 && (
        <div className="check-options" role="group" aria-label={name}>
          {visible.map((o) => (
            <label key={o} className={known.has(o) ? undefined : "absent"}>
              <input type="checkbox" checked={value.includes(o)} onChange={(e) => toggle(o, e.target.checked)} />
              <span>{o}</span>
              {!known.has(o) && <small className="muted">{t("absent en amont")}</small>}
            </label>
          ))}
          {!visible.length && <small className="muted">{t("Aucun résultat.")}</small>}
        </div>
      )}
      {value.length > 1 && <small className="muted check-order">{t("Ordre : {{list}}", { list: value.join(", ") })}</small>}
      {free && (
        <div className="list-row">
          <input
            type="text"
            value={typed}
            placeholder={options.length ? t("Autre attribut…") : t("Nom d'attribut (amont pas encore connu)…")}
            aria-label={t("{{label}} : saisir un nom", { label: name })}
            onChange={(e) => setTyped(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                add();
              }
            }}
          />
          <button type="button" onClick={add} disabled={!typed.trim()}>
            {t("+ Ajouter")}
          </button>
        </div>
      )}
    </div>
  );
}

function StringList({ item, value, onChange, ctx, label }: { item: JsonSchema; value: string[]; onChange: (v: unknown) => void; ctx: FormCtx; label?: string }) {
  const widget = widgetOf(item);
  return (
    <div className="list">
      {value.map((v, i) => (
        <div className="list-row" key={i}>
          <TextEditor widget={widget} value={v} ctx={ctx} compact label={`${label ?? t("Valeur")} ${i + 1}`} onChange={(x) => onChange(value.map((y, j) => (j === i ? x : y)))} />
          <button type="button" aria-label={t("Supprimer la ligne")} onClick={() => onChange(value.filter((_, j) => j !== i))}>
            ×
          </button>
        </div>
      ))}
      <button type="button" className="add" onClick={() => onChange([...value, ""])}>
        {t("+ Ajouter")}
      </button>
    </div>
  );
}

function ObjectTable({ schema, rawItem, value, onChange, ctx }: { schema: JsonSchema; rawItem: JsonSchema; value: unknown[]; onChange: (v: unknown) => void; ctx: FormCtx }) {
  const columns = Object.entries(schema.properties ?? {});
  const rows = value as Record<string, unknown>[];
  return (
    <div className="table-editor">
      {rows.map((row, i) => (
        <div className="row-card" key={i}>
          {columns.map(([key, raw]) => {
            const s = resolveSchema(raw, ctx.defs);
            return (
              <div className="cell" key={key}>
                <span className="cell-label">{labelOf(key, s)}</span>
                <Editor
                  label={labelOf(key, s)}
                  schema={raw}
                  compact
                  ctx={ctx}
                  value={row[key] ?? defaultValue(raw, ctx.defs)}
                  onChange={(v) => onChange(rows.map((r, j) => (j === i ? { ...r, [key]: v } : r)))}
                />
              </div>
            );
          })}
          <button type="button" className="remove" aria-label={t("Supprimer la ligne")} onClick={() => onChange(rows.filter((_, j) => j !== i))}>
            ×
          </button>
        </div>
      ))}
      <button type="button" className="add" onClick={() => onChange([...rows, defaultValue(rawItem, ctx.defs)])}>
        {t("+ Ajouter une ligne")}
      </button>
    </div>
  );
}

const asArray = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

/** « schema.table » choisi dans deux listes lues sur la connexion : schémas, puis tables et vues du schéma (✱ : avec
 * géométrie). En écriture, la table peut aussi être tapée (elle sera créée). Sans liste (connexion absente ou
 * injoignable), le champ reste une saisie libre. */
function PgTableSelect({ connection, value, onChange, label, write }: { connection: string; value: string; onChange: (v: string) => void; label: string; write: boolean }) {
  const [tables, setTables] = useState<PgTableInfo[] | null>(null);
  const [error, setError] = useState("");
  const listId = useId();
  useEffect(() => {
    setTables(null);
    setError("");
    if (!connection) return;
    let live = true;
    api
      .connectionTables(connection)
      .then((list) => live && setTables(list))
      .catch((err: unknown) => live && setError(err instanceof ApiError ? err.message : t("Liste des tables indisponible")));
    return () => {
      live = false;
    };
  }, [connection]);
  const dot = value.indexOf(".");
  const schema = dot >= 0 ? value.slice(0, dot) : "";
  const table = dot >= 0 ? value.slice(dot + 1) : value;
  const [picked, setPicked] = useState(schema); // schéma choisi avant la table
  useEffect(() => {
    if (schema) setPicked(schema);
  }, [schema]);
  const schemas = useMemo(() => [...new Set((tables ?? []).map((x) => x.schema))], [tables]);
  const inSchema = (tables ?? []).filter((x) => x.schema === picked);
  const known = inSchema.some((x) => x.table === table);
  const free = <input type="text" aria-label={label} value={value} placeholder="public.communes" onChange={(e) => onChange(e.target.value)} />;

  if (!connection) return <>{free}<small className="muted">{t("Choisir d'abord une connexion : ses schémas et tables seront proposés.")}</small></>;
  if (error) return <>{free}<small className="error-text">{error}</small></>;
  if (!tables) return <span className="pg-loading"><Spinner size={12} /> {t("Lecture des tables…")}</span>;
  return (
    <div className="pg-table">
      <select
        aria-label={t("{{label}} : schéma", { label })}
        value={picked}
        onChange={(e) => {
          // autre schéma : la table est à rechoisir (en écriture, le nom tapé est gardé)
          const next = e.target.value;
          setPicked(next);
          onChange(write && next && table ? `${next}.${table}` : "");
        }}
      >
        <option value="">{t("— schéma —")}</option>
        {picked && !schemas.includes(picked) && <option value={picked}>{t("{{name}} (inconnu)", { name: picked })}</option>}
        {schemas.map((x) => (
          <option key={x} value={x}>{x}</option>
        ))}
      </select>
      {write ? (
        <>
          <input type="text" aria-label={t("{{label}} : table", { label })} list={listId} value={table} disabled={!picked} placeholder={t("table existante ou nouvelle")} onChange={(e) => onChange(e.target.value ? `${picked}.${e.target.value}` : "")} />
          <datalist id={listId}>
            {inSchema.map((x) => (
              <option key={x.table} value={x.table} />
            ))}
          </datalist>
        </>
      ) : (
        <select aria-label={t("{{label}} : table", { label })} value={known ? table : table ? `?${table}` : ""} disabled={!picked} onChange={(e) => onChange(e.target.value ? `${picked}.${e.target.value}` : "")}>
          <option value="">{t("— table —")}</option>
          {table && !known && <option value={`?${table}`}>{t("{{name}} (inconnue)", { name: table })}</option>}
          {inSchema.map((x) => (
            <option key={x.table} value={x.table} title={t("≈ {{count}} lignes", { count: formatCount(x.rows_estimate) })}>
              {x.table}
              {x.geometry ? " ✱" : ""}
              {x.kind === "v" || x.kind === "m" ? ` (${t("vue")})` : ""}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}

/** Connexions nommées ; `kinds` : seulement celles de ces types de base (lecteur Oracle…). */
function ConnectionSelect({ value, onChange, label, kinds }: { value: string; onChange: (v: string) => void; label?: string; kinds?: string[] }) {
  const { list: all, loaded, refresh } = useConnections();
  const list = kinds ? all.filter((c) => kinds.includes(c.kind || "postgres")) : all;
  const setOpen = useUi((u) => u.setConnectionsOpen);
  useEffect(() => {
    if (!loaded) void refresh();
  }, [loaded, refresh]);
  const known = list.some((c) => c.name === value);
  return (
    <div className="conn-select">
      <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{t("— choisir —")}</option>
        {value && !known && <option value={value}>{t("{{name}} (inconnue)", { name: value })}</option>}
        {list.map((c) => (
          <option key={c.name} value={c.name}>
            {kinds?.length === 1 ? c.name : `${c.name} · ${kindLabel(c.kind)}`}
          </option>
        ))}
      </select>
      <button type="button" title={t("Gérer les connexions")} onClick={() => setOpen(true)}>
        …
      </button>
    </div>
  );
}
