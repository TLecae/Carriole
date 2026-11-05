import { useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, api } from "../api/client";
import type { TableColumns as Described, ValueCount } from "../api/types";
import { formatCount, t } from "../i18n";
import { toFlowDoc } from "../model/graph";
import { bySchema, containsWhere, literal, quoteName, tableName, type HelperField, type HelperSource } from "../model/fieldHelper";
import { useEditor } from "../store/editor";
import { Spinner } from "./Spinner";

/** Colonne dont on affiche les valeurs : sa table (base) ou son entrée (nœud). */
type Picked = { column: string; table?: string; input?: number };

/**
 * Aide à la saisie à côté d'un éditeur : les attributs (métadonnées : tables et colonnes d'une base, attributs des
 * entrées, colonnes d'un fichier) et, à la demande, leurs valeurs les plus fréquentes. Un clic insère le nom ou la
 * valeur au curseur, écrits comme SQL les attend : on construit sa requête sans deviner une orthographe.
 */
export function FieldHelper({ source: given, tables, onInsert }: { source: HelperSource; tables?: Record<string, string[]>; onInsert: (text: string) => void }) {
  // une requête en cours de saisie est souvent invalide, et l'analyse perd alors le schéma du nœud : les derniers
  // attributs connus restent affichés
  const known = useRef(given);
  const empty = (s: HelperSource) => (s.kind === "reader" ? !s.fields.length : s.kind === "inputs" ? !s.inputs.some((i) => i.fields.length) : false);
  if (!empty(given) || given.kind !== known.current.kind) known.current = given;
  const source = known.current;
  const [filter, setFilter] = useState("");
  const [openSchemas, setOpenSchemas] = useState<Set<string>>(() => new Set()); // schémas dépliés (base)
  const [openTables, setOpenTables] = useState<Set<string>>(() => new Set()); // tables dépliées
  const [picked, setPicked] = useState<Picked | null>(null);
  const needle = filter.trim().toLowerCase();
  const match = (name: string) => !needle || name.toLowerCase().includes(needle);

  const groups = source.kind === "connection" && tables ? bySchema(tables, needle) : [];

  // `all` : toutes les colonnes, même hors de la recherche (elle désigne la table, pas une colonne)
  const columns = (fields: HelperField[], at: Omit<Picked, "column">, all = false) =>
    fields.filter((f) => all || match(f.name)).map((f) => <Column key={f.name} field={f} at={at} picked={picked} setPicked={setPicked} source={source} onInsert={onInsert} />);

  return (
    <aside className="field-helper" aria-label={t("Attributs et valeurs")}>
      <input type="search" value={filter} placeholder={t("Rechercher un attribut…")} aria-label={t("Rechercher un attribut…")} onChange={(e) => setFilter(e.target.value)} />
      <p className="muted field-helper-hint">{t("Clic : insérer au curseur. ▾ : ses valeurs.")}</p>
      <div className="field-helper-list">
        {source.kind === "connection" &&
          (!tables ? (
            <p className="muted">{t("Lecture des tables…")}</p>
          ) : (
            groups.map((group) => {
              // un seul schéma, ou une recherche : tout est déplié
              const unfolded = !group.schema || groups.length === 1 || !!needle || openSchemas.has(group.schema);
              return (
                <div key={group.schema} className="helper-schema">
                  {group.schema && (
                    <div className="helper-row">
                      <button type="button" className="helper-toggle" aria-expanded={unfolded} onClick={() => setOpenSchemas(flip(openSchemas, group.schema))} aria-label={t("Tables du schéma {{name}}", { name: group.schema })}>
                        {unfolded ? "▾" : "▸"}
                      </button>
                      <button type="button" className="helper-name schema" onClick={() => setOpenSchemas(flip(openSchemas, group.schema))} title={t("Schéma : afficher ou masquer ses tables")}>
                        {group.schema}
                      </button>
                      <small className="muted helper-type" title={t("Tables et vues")}>
                        {group.tables.length}
                      </small>
                    </div>
                  )}
                  {unfolded && (
                    <div className={group.schema ? "helper-tables" : undefined}>
                      {group.tables.slice(0, 300).map(({ name, short, columns: cols }) => {
                        const hit = !!needle && cols.some(match); // la recherche porte sur une de ses colonnes
                        const shown = openTables.has(name) || hit;
                        return (
                          <div key={name} className="helper-table">
                            <div className="helper-row">
                              <button type="button" className="helper-toggle" aria-expanded={shown} onClick={() => setOpenTables(flip(openTables, name))} aria-label={t("Colonnes de {{name}}", { name })}>
                                {shown ? "▾" : "▸"}
                              </button>
                              <button type="button" className="helper-name table" onClick={() => onInsert(tableName(name))} title={t("Insérer la table {{name}}", { name })}>
                                {short}
                              </button>
                            </div>
                            {shown && (
                              <div className="helper-columns">
                                <TableColumns connection={source.connection} table={name} known={cols} render={(fields) => columns(fields, { table: name }, !hit)} />
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })
          ))}
        {source.kind === "inputs" &&
          (source.inputs.length === 0 ? (
            <p className="muted">{t("Attributs reçus inconnus : relier l'entrée, ou lancer le flux en amont.")}</p>
          ) : (
            source.inputs.map((input, i) => (
              <div key={input.port + i} className="helper-table">
                {source.inputs.length > 1 && (
                  <div className="helper-row">
                    <button type="button" className="helper-name table" onClick={() => onInsert(input.port)} title={t("Insérer le nom de l'entrée")}>
                      {input.port}
                    </button>
                    <small className="muted">{input.label}</small>
                  </div>
                )}
                {columns(input.fields, { input: i })}
              </div>
            ))
          ))}
        {source.kind === "reader" &&
          (source.fields.length ? columns(source.fields, {}) : <p className="muted">{t("Colonnes du fichier inconnues : choisir un fichier lisible.")}</p>)}
      </div>
    </aside>
  );
}

/** Ensemble avec `key` ajouté ou retiré (état React : un nouvel objet). */
function flip(set: Set<string>, key: string): Set<string> {
  const next = new Set(set);
  if (!next.delete(key)) next.add(key);
  return next;
}

// colonnes et types d'une table, lus une fois par session de l'éditeur (la complétion n'a que les noms)
const described = new Map<string, Promise<Described>>();

/** Colonnes d'une table dépliée, avec leurs types : celles de la complétion d'abord, puis celles lues sur la base
 * (toutes, même pour une base dont la complétion n'a pas chargé les colonnes). */
function TableColumns({ connection, table, known, render }: { connection: string; table: string; known: string[]; render: (fields: HelperField[]) => ReactNode }) {
  const [fields, setFields] = useState<HelperField[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    const key = `${connection}|${table}`;
    if (!described.has(key)) described.set(key, api.tableColumns(connection, table));
    described
      .get(key)!
      .then((r) => live && setFields(r.columns))
      .catch((err: unknown) => {
        described.delete(key);
        if (live) setError(err instanceof ApiError ? err.message : String(err));
      });
    return () => {
      live = false;
    };
  }, [connection, table]);
  const shown = fields ?? known.map((name) => ({ name, type: "" }));
  return (
    <>
      {render(shown)}
      {!fields && !error && !known.length && (
        <p className="muted">
          <Spinner size={11} /> {t("Lecture des colonnes…")}
        </p>
      )}
      {error && !known.length && <p className="error-text">{error}</p>}
    </>
  );
}

function Column({ field, at, picked, setPicked, source, onInsert }: { field: HelperField; at: Omit<Picked, "column">; picked: Picked | null; setPicked: (p: Picked | null) => void; source: HelperSource; onInsert: (text: string) => void }) {
  const here = !!picked && picked.column === field.name && picked.table === at.table && picked.input === at.input;
  const geometry = field.name === "geometry" || /geometry|wkb|blob|binary/i.test(field.type);
  return (
    <div className="helper-column">
      <div className="helper-row">
        <button type="button" className="helper-toggle" disabled={geometry} aria-expanded={here} aria-label={t("Valeurs de {{name}}", { name: field.name })} title={geometry ? t("Pas de valeurs proposées pour une géométrie") : t("Valeurs les plus fréquentes")} onClick={() => setPicked(here ? null : { column: field.name, ...at })}>
          {here ? "▾" : "▸"}
        </button>
        <button type="button" className="helper-name" onClick={() => onInsert(quoteName(field.name))} title={t("Insérer l'attribut")}>
          {field.name}
        </button>
        {field.type && <small className="muted helper-type">{field.type}</small>}
      </div>
      {here && <Values source={source} picked={picked} onInsert={onInsert} />}
    </div>
  );
}

function Values({ source, picked, onInsert }: { source: HelperSource; picked: Picked; onInsert: (text: string) => void }) {
  const [search, setSearch] = useState("");
  const [values, setValues] = useState<ValueCount[] | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  // ce qui désigne les valeurs (l'objet `source` change à chaque saisie dans l'éditeur, sans changer de valeurs)
  const input = source.kind === "inputs" ? source.inputs[picked.input ?? 0] : undefined;
  const key = JSON.stringify([source.kind, source.kind === "connection" ? source.connection : source.kind === "reader" ? source.node : input?.ref, picked.table, picked.column]);

  useEffect(() => {
    let live = true;
    setError("");
    const timer = setTimeout(() => {
      setValues(null);
      load(source, picked, search)
        .then(([list, origin]) => {
          if (!live) return;
          setValues(list);
          setNote(origin);
        })
        .catch((err: unknown) => {
          if (!live) return;
          setValues([]);
          setError(err instanceof ApiError || err instanceof Error ? err.message : String(err));
        });
    }, search ? 300 : 0);
    return () => {
      live = false;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `key` résume `source` et `picked`
  }, [key, search]);

  return (
    <div className="helper-values">
      <input type="search" value={search} placeholder={t("Chercher une valeur…")} aria-label={t("Chercher une valeur de {{name}}", { name: picked.column })} onChange={(e) => setSearch(e.target.value)} />
      {!values && !error && (
        <p className="muted">
          <Spinner size={11} /> {t("Lecture des valeurs…")}
        </p>
      )}
      {error && <p className="error-text">{error}</p>}
      {values && !error && !values.length && <p className="muted">{t("Aucune valeur.")}</p>}
      {values && values.length > 0 && (
        <ul>
          {values.map((v, i) => (
            <li key={i}>
              <button type="button" onClick={() => onInsert(literal(v.value))} title={t("Insérer la valeur")}>
                <span className="helper-value">{v.value === null ? t("(vide)") : String(v.value)}</span>
                <small className="muted">{formatCount(v.count)}</small>
              </button>
            </li>
          ))}
        </ul>
      )}
      {note && <p className="muted helper-note">{note}</p>}
    </div>
  );
}

/** Valeurs les plus fréquentes et d'où elles viennent. */
async function load(source: HelperSource, picked: Picked, search: string): Promise<[ValueCount[], string]> {
  if (source.kind === "connection") {
    return [await api.connectionValues(source.connection, picked.table ?? "", picked.column, search), t("Valeurs lues dans la table.")];
  }
  if (source.kind === "reader") {
    const { meta, nodes, edges } = useEditor.getState();
    return [await api.readerValues(toFlowDoc(meta, nodes, edges), source.node, picked.column, search), t("Valeurs lues dans le fichier.")];
  }
  const input = source.inputs[picked.input ?? 0];
  if (!input?.ref) throw new Error(t("Valeurs connues après un run : lancer le flux jusqu'au nœud en amont."));
  const where = containsWhere(picked.column, search);
  return [await api.dataValues(input.ref, picked.column, { filters: [], where, search: "", sort: [] }, 50), t("Valeurs du dernier run.")];
}
