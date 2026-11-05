import { useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { SchemaInfo, TableColumns } from "../api/types";
import { t } from "../i18n";
import { Spinner } from "./Spinner";

/** Modes qui écrivent dans la table telle qu'elle est (les autres la créent ou la recréent depuis les données). */
const INTO_EXISTING = new Set(["append", "truncate", "upsert"]);

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
        .catch((err: unknown) => live && setError(err instanceof ApiError ? err.message : t("Colonnes de la table indisponibles")));
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
  const fold = (name: string) => (info?.case_sensitive ? name : name.toLowerCase());
  const columns = new Set((info?.columns ?? []).map((c) => fold(c.name)));
  const absent = incoming.filter((name) => !columns.has(fold(name)));
  const fed = new Set(incoming.map(fold));
  const unfed = (info?.columns ?? []).filter((c) => !fed.has(fold(c.name)));

  return (
    <section className="table-fields" data-testid="table-fields">
      <h3>{t("Champs écrits dans la table")}</h3>
      {error && <p className="error-text">{error}</p>}
      {!info && !error && (
        <p className="pg-loading">
          <Spinner size={12} /> {t("Lecture des colonnes…")}
        </p>
      )}
      {info && !info.exists && <p className="muted">{t("La table n'existe pas encore : elle sera créée avec les attributs reçus.")}</p>}
      {info?.exists && !known.length && <p className="muted">{t("Attributs reçus inconnus : relier l'entrée, ou lancer le flux en amont.")}</p>}
      {info?.exists && known.length > 0 && (
        <>
          {absent.length > 0 && (
            <p className="error-text" role="alert">
              {t("{{count}} attribut(s) absent(s) de la table : ils ne peuvent pas y être écrits (à renommer ou retirer avant l'écriture).", { count: absent.length })}
            </p>
          )}
          <ul className="field-match">
            {incoming.map((name) => {
              const missing = absent.includes(name);
              return (
                <li key={name} className={missing ? "absent" : "ok"} title={missing ? t("Absent de la table : ne peut pas être écrit") : t("Colonne de la table")}>
                  <span>{name}</span>
                  <small>{missing ? t("absent de la table") : "✓"}</small>
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
