import { useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { SchemaInfo, TableColumns } from "../api/types";
import { t } from "../i18n";
import { toFlowDoc } from "../model/graph";
import { useEditor } from "../store/editor";
import { Spinner } from "./Spinner";

/** Modes qui écrivent dans la table telle qu'elle est (les autres la créent ou la recréent depuis les données). */
const INTO_EXISTING = new Set(["append", "truncate", "upsert"]);

/** Textes du panneau : une table de base, ou une couche de fichier (feuille Excel, en-tête de CSV). */
const TEXTS = {
  table: {
    title: () => t("Champs écrits dans la table"),
    unavailable: () => t("Colonnes de la table indisponibles"),
    created: () => t("La table n'existe pas encore : elle sera créée avec les attributs reçus."),
    absent: (count: number) => t("{{count}} attribut(s) absent(s) de la table : ils ne peuvent pas y être écrits (à renommer ou retirer avant l'écriture).", { count }),
    missingTitle: () => t("Absent de la table : ne peut pas être écrit"),
    missing: () => t("absent de la table"),
    column: () => t("Colonne de la table"),
  },
  layer: {
    title: () => t("Champs écrits dans la couche"),
    unavailable: () => t("Colonnes de la couche indisponibles"),
    created: () => t("La couche n'existe pas encore : elle sera créée avec les attributs reçus."),
    absent: (count: number) => t("{{count}} attribut(s) absent(s) de la couche : ils ne peuvent pas y être écrits (à renommer ou retirer avant l'écriture).", { count }),
    missingTitle: () => t("Absent de la couche : ne peut pas être écrit"),
    missing: () => t("absent de la couche"),
    column: () => t("Colonne de la couche"),
  },
};

/**
 * Écriture dans une table existante : les attributs qui arrivent, face aux colonnes de la table. Ceux qu'elle ne peut
 * pas recevoir sont en rouge (le run s'arrêterait sur « colonnes absentes de la table ») ; les colonnes que rien
 * n'alimente restent vides.
 */
export function TableFields({ params, inputs }: { params: Record<string, unknown>; inputs: (SchemaInfo | null)[] }) {
  const connection = String(params.connection ?? "");
  const table = String(params.table ?? "");
  const mode = String(params.mode ?? "append");
  const geometryColumn = String(params.geometry_column ?? "geom") || "geom";
  const [info, setInfo] = useState<TableColumns | null>(null);
  const [error, setError] = useState("");
  const active = !!connection && !!table && INTO_EXISTING.has(mode);

  useEffect(() => {
    setInfo(null);
    setError("");
    if (!active) return;
    let live = true;
    const timer = setTimeout(() => {
      api
        .tableColumns(connection, table)
        .then((r) => live && setInfo(r))
        .catch((err: unknown) => live && setError(err instanceof ApiError ? err.message : TEXTS.table.unavailable()));
    }, 400); // le nom de la table se tape : une requête quand la saisie s'arrête
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [active, connection, table]);

  if (!active) return null;
  const known = inputs.filter((s): s is SchemaInfo => !!s);
  const incoming = [...new Set(known.flatMap((s) => s.fields.map((f) => f.name)))];
  if (known.some((s) => s.geometry)) incoming.push(geometryColumn); // la géométrie s'écrit dans cette colonne
  return <FieldMatch kind="table" info={info} error={error} incoming={known.length ? incoming : null} />;
}

/**
 * Écriture en ajout dans un fichier (GeoPackage, Shapefile, CSV, Excel…) : les attributs reçus, nommés comme ils
 * seraient écrits (Shapefile : 10 caractères), face aux colonnes de la couche visée. Le serveur fait la comparaison
 * que fera l'écriture.
 */
export function FileFields({ id, params, inputs }: { id: string; params: Record<string, unknown>; inputs: (SchemaInfo | null)[] }) {
  const path = String(params.path ?? "");
  const active = !!path && params.mode === "append";
  const [info, setInfo] = useState<TableColumns | null>(null);
  const [error, setError] = useState("");
  // à refaire si la cible ou les attributs reçus changent
  const key = JSON.stringify([path, params.layer, params.sheet, params.delimiter, inputs.map((s) => s && [s.fields.map((f) => f.name), !!s.geometry])]);

  useEffect(() => {
    setInfo(null);
    setError("");
    if (!active) return;
    let live = true;
    const timer = setTimeout(() => {
      const { meta, nodes, edges } = useEditor.getState();
      api
        .writerTarget(toFlowDoc(meta, nodes, edges), id)
        .then((r) => live && setInfo(r))
        .catch((err: unknown) => live && setError(err instanceof ApiError ? err.message : TEXTS.layer.unavailable()));
    }, 400); // le chemin se tape : une requête quand la saisie s'arrête
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [active, id, key]);

  if (!active) return null;
  return <FieldMatch kind="layer" info={info} error={error} incoming={info?.incoming ?? null} />;
}

function FieldMatch({ kind, info, error, incoming }: { kind: keyof typeof TEXTS; info: TableColumns | null; error: string; incoming: string[] | null }) {
  const text = TEXTS[kind];
  const fold = (name: string) => (info?.case_sensitive ? name : name.toLowerCase());
  const columns = new Set((info?.columns ?? []).map((c) => fold(c.name)));
  const names = incoming ?? [];
  const absent = names.filter((name) => !columns.has(fold(name)));
  const fed = new Set(names.map(fold));
  const unfed = (info?.columns ?? []).filter((c) => !fed.has(fold(c.name)));

  return (
    <section className="table-fields" data-testid="table-fields">
      <h3>{text.title()}</h3>
      {error && <p className="error-text">{error}</p>}
      {!info && !error && (
        <p className="pg-loading">
          <Spinner size={12} /> {t("Lecture des colonnes…")}
        </p>
      )}
      {info && !info.exists && <p className="muted">{text.created()}</p>}
      {info?.exists && !incoming && <p className="muted">{t("Attributs reçus inconnus : relier l'entrée, ou lancer le flux en amont.")}</p>}
      {info?.exists && incoming && (
        <>
          {absent.length > 0 && (
            <p className="error-text" role="alert">
              {text.absent(absent.length)}
            </p>
          )}
          <ul className="field-match">
            {names.map((name) => {
              const missing = absent.includes(name);
              return (
                <li key={name} className={missing ? "absent" : "ok"} title={missing ? text.missingTitle() : text.column()}>
                  <span>{name}</span>
                  <small>{missing ? text.missing() : "✓"}</small>
                </li>
              );
            })}
            {unfed.map((c) => (
              <li key={`col-${c.name}`} className="unfed" title={t("Colonne que rien n'alimente : laissée vide")}>
                <span>{c.name}</span>
                <small>{t("non alimentée")}</small>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
