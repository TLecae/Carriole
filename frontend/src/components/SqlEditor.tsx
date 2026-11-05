import { Suspense, lazy, useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, api } from "../api/client";
import type { SqlCheckResult } from "../api/types";
import { t } from "../i18n";
import { completionSchema, type HelperSource } from "../model/fieldHelper";
import { useConnections } from "../store/connections";
import { Modal } from "./Dialogs";
import { FieldHelper } from "./FieldHelper";
import { Spinner } from "./Spinner";

const CodeEditor = lazy(() => import("./CodeEditor"));

const DIALECT_LABEL: Record<string, string> = { postgres: "PostgreSQL", oracle: "Oracle", duckdb: "DuckDB" };

/** Ce que l'on écrit : une requête entière, la condition d'un WHERE, ou une expression (valeur, condition). */
export type QueryMode = "sql" | "where" | "expression";

/**
 * Champ SQL : une zone de texte, et un bouton qui ouvre l'éditeur en surcouche pour une requête longue — coloration et
 * complétion selon la base, erreurs soulignées par le serveur, attributs et valeurs à insérer (`helper`).
 * `connection` : la base où le SQL s'exécute ; sans elle, c'est du SQL DuckDB.
 */
export function SqlField({ label, value, onChange, connection, where = false, helper, placeholder, preview }: { label: string; value: string; onChange: (v: string) => void; connection?: string; where?: boolean; helper?: HelperSource; placeholder?: string; preview?: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="sql-field">
      <div className="sql-line">
        <textarea className="sql-text" aria-label={label} rows={where ? 1 : 3} value={value} placeholder={placeholder} spellCheck={false} onChange={(e) => onChange(e.target.value)} />
        <EditorButton onClick={() => setOpen(true)} />
      </div>
      {preview}
      {open && <QueryEditor label={label} value={value} onChange={onChange} connection={connection} mode={where ? "where" : "sql"} helper={helper ?? (connection ? { kind: "connection", connection } : undefined)} onClose={() => setOpen(false)} />}
    </div>
  );
}

export function EditorButton({ onClick, compact = false }: { onClick: () => void; compact?: boolean }) {
  return (
    <button type="button" className={compact ? "sql-open compact" : "sql-open"} onClick={onClick} title={t("Ouvrir l'éditeur : toute la requête, attributs et valeurs à insérer, vérification")} aria-label={t("Éditeur SQL")}>
      {compact ? "⤢" : t("Éditeur SQL")}
    </button>
  );
}

/** Éditeur en surcouche : le texte à gauche, les attributs et leurs valeurs à droite. */
export function QueryEditor({ label, value, onChange, connection, mode, helper, onClose }: { label: string; value: string; onChange: (v: string) => void; connection?: string; mode: QueryMode; helper?: HelperSource; onClose: () => void }) {
  const kind = useConnections((s) => s.list.find((c) => c.name === connection)?.kind);
  const [tables, setTables] = useState<Record<string, string[]> | undefined>();
  const [dialect, setDialect] = useState<string>(connection ? (kind ?? "postgres") : "duckdb");
  const [loading, setLoading] = useState(!!connection);
  const [loadError, setLoadError] = useState("");
  const [result, setResult] = useState<SqlCheckResult | null>(null);
  const [checking, setChecking] = useState(false);
  const inserter = useRef<((text: string) => void) | null>(null);

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

  const check = queryCheck(mode, connection);
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
  const schema = helper ? completionSchema(helper, tables) : tables;

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
      <div className={helper ? "query-body with-helper" : "query-body"}>
        <Suspense fallback={<textarea aria-label={label} rows={16} value={value} onChange={(e) => onChange(e.target.value)} />}>
          <CodeEditor value={value} language="sql" onChange={onChange} label={label} dialect={dialect} schema={schema} check={check} inserter={inserter} tall />
        </Suspense>
        {helper && <FieldHelper source={helper} tables={tables} onInsert={(text) => (inserter.current ? inserter.current(text) : onChange(value + text))} />}
      </div>
      <div className="modal-actions sql-actions">
        <span className="sql-result" role="status">
          {checking ? (
            <Spinner size={12} />
          ) : result?.skipped ? (
            <span className="muted">{skippedText(result.skipped)}</span>
          ) : result?.ok ? (
            <span className="ok-text">{mode === "sql" ? t("Requête valide") : t("Expression valide")}</span>
          ) : result ? (
            <span className="error-text">{result.line ? t("Ligne {{line}} : {{error}}", { line: result.line, error: result.error ?? "" }) : result.error}</span>
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

/** Vérification par le serveur. Un filtre WHERE ou une expression se vérifient dans une requête complète ; l'erreur
 * revient à sa colonne dans le texte saisi. */
export function queryCheck(mode: QueryMode, connection = ""): (sql: string) => Promise<SqlCheckResult> {
  const prefix = mode === "where" ? "SELECT * FROM t WHERE " : mode === "expression" ? "SELECT " : "";
  return async (sql) => {
    const r = await api.checkSql(prefix && sql.trim() ? `${prefix}${sql}${mode === "expression" ? " FROM t" : ""}` : sql, connection);
    if (!prefix || !r.line) return r;
    return { ...r, column: r.line === 1 && r.column ? Math.max(1, r.column - prefix.length) : r.column };
  };
}

function skippedText(reason: NonNullable<SqlCheckResult["skipped"]>): string {
  if (reason === "variables") return t("Non vérifiée : la requête contient des variables ${…}, connues au lancement.");
  if (reason === "statement") return t("Non vérifiée : seuls SELECT, WITH, INSERT, UPDATE, DELETE et MERGE se vérifient sans être exécutés.");
  return t("Cette base ne permet pas de vérifier une requête sans l'exécuter.");
}
