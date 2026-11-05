import { Suspense, lazy, useEffect, useState, type ReactNode } from "react";
import { ApiError, api } from "../api/client";
import type { SqlCheckResult } from "../api/types";
import { t } from "../i18n";
import { useConnections } from "../store/connections";
import { Modal } from "./Dialogs";
import { Spinner } from "./Spinner";

const CodeEditor = lazy(() => import("./CodeEditor"));

const DIALECT_LABEL: Record<string, string> = { postgres: "PostgreSQL", oracle: "Oracle", duckdb: "DuckDB" };

/**
 * Champ SQL : une zone de texte, et un bouton qui ouvre l'éditeur SQL en surcouche pour une requête longue —
 * coloration et complétion selon la base (mots-clés, tables, colonnes), erreurs soulignées par le serveur.
 * `connection` : la base où le SQL s'exécute ; sans elle, c'est du SQL DuckDB (`schema` : tables qu'il voit).
 */
export function SqlField({ label, value, onChange, connection, where = false, schema, placeholder, preview }: { label: string; value: string; onChange: (v: string) => void; connection?: string; where?: boolean; schema?: Record<string, string[]>; placeholder?: string; preview?: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="sql-field">
      <div className="sql-line">
        <textarea className="sql-text" aria-label={label} rows={where ? 1 : 3} value={value} placeholder={placeholder} spellCheck={false} onChange={(e) => onChange(e.target.value)} />
        <button type="button" className="sql-open" onClick={() => setOpen(true)} title={t("Ouvrir l'éditeur SQL : toute la requête, complétion, vérification")}>
          {t("Éditeur SQL")}
        </button>
      </div>
      {preview}
      {open && <SqlOverlay label={label} value={value} onChange={onChange} connection={connection} schema={schema} where={where} onClose={() => setOpen(false)} />}
    </div>
  );
}

export function SqlOverlay({ label, value, onChange, connection, schema, where, onClose }: { label: string; value: string; onChange: (v: string) => void; connection?: string; schema?: Record<string, string[]>; where?: boolean; onClose: () => void }) {
  const kind = useConnections((s) => s.list.find((c) => c.name === connection)?.kind);
  const [tables, setTables] = useState<Record<string, string[]> | undefined>(schema);
  const [dialect, setDialect] = useState<string>(connection ? (kind ?? "postgres") : "duckdb");
  const [loading, setLoading] = useState(!!connection);
  const [loadError, setLoadError] = useState("");
  const [result, setResult] = useState<SqlCheckResult | null>(null);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    if (!connection) return;
    let live = true;
    api
      .connectionSchema(connection)
      .then((r) => {
        if (!live) return;
        setTables(r.tables);
        setDialect(r.dialect);
      })
      .catch((err: unknown) => live && setLoadError(err instanceof ApiError ? err.message : t("Tables de la base indisponibles")))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [connection]);

  // un filtre WHERE se vérifie dans une requête complète
  const wrap = (sql: string) => (where && sql.trim() ? `SELECT * FROM t WHERE ${sql}` : sql);
  const check = async (sql: string): Promise<SqlCheckResult> => {
    const r = await api.checkSql(wrap(sql), connection ?? "");
    if (!where || !r.line) return r;
    return { ...r, line: 1, column: r.line === 1 && r.column ? Math.max(1, r.column - "SELECT * FROM t WHERE ".length) : null };
  };
  const verify = async () => {
    setChecking(true);
    try {
      setResult(await check(value));
    } catch (err) {
      setResult({ ok: false, error: err instanceof ApiError ? err.message : String(err), line: null, column: null, skipped: null });
    } finally {
      setChecking(false);
    }
  };

  return (
    <Modal title={t("Éditeur SQL — {{label}}", { label })} onClose={onClose} className="wide sql-modal">
      <p className="sql-meta muted">
        {t("Dialecte : {{name}}", { name: DIALECT_LABEL[dialect] ?? dialect })}
        {connection ? ` · ${t("connexion « {{name}} »", { name: connection })}` : ""}
        {loading && (
          <>
            {" · "}
            <Spinner size={11} /> {t("Lecture des tables…")}
          </>
        )}
        {loadError && <span className="error-text"> · {loadError}</span>}
        {" · "}
        {t("Ctrl+Espace : compléter")}
      </p>
      <Suspense fallback={<textarea aria-label={label} rows={16} value={value} onChange={(e) => onChange(e.target.value)} />}>
        <CodeEditor value={value} language="sql" onChange={onChange} label={label} dialect={dialect} schema={tables} check={check} tall />
      </Suspense>
      <div className="modal-actions sql-actions">
        <span className="sql-result" role="status">
          {checking ? (
            <Spinner size={12} />
          ) : result?.skipped ? (
            <span className="muted">{skippedText(result.skipped)}</span>
          ) : result?.ok ? (
            <span className="ok-text">{t("Requête valide")}</span>
          ) : result ? (
            <span className="error-text">
              {result.line ? t("Ligne {{line}} : {{error}}", { line: result.line, error: result.error ?? "" }) : result.error}
            </span>
          ) : null}
        </span>
        <button type="button" onClick={() => void verify()} disabled={checking}>
          {t("Vérifier")}
        </button>
        <button type="button" className="primary" onClick={onClose}>
          {t("Fermer")}
        </button>
      </div>
    </Modal>
  );
}

function skippedText(reason: NonNullable<SqlCheckResult["skipped"]>): string {
  if (reason === "variables") return t("Non vérifiée : la requête contient des variables ${…}, connues au lancement.");
  if (reason === "statement") return t("Non vérifiée : seuls SELECT, WITH, INSERT, UPDATE, DELETE et MERGE se vérifient sans être exécutés.");
  return t("Cette base ne permet pas de vérifier une requête sans l'exécuter.");
}
