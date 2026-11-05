import { Suspense, lazy, useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, api } from "../api/client";
import { formatNumber, t } from "../i18n";
import { quoteName, type HelperField } from "../model/fieldHelper";
import { toFlowDoc } from "../model/graph";
import { useEditor } from "../store/editor";
import { Modal } from "./Dialogs";
import { FieldHelper } from "./FieldHelper";
import { Spinner } from "./Spinner";
import { queryCheck } from "./SqlEditor";

const CodeEditor = lazy(() => import("./CodeEditor"));

/** Paramètres du lecteur qui changent le fichier lu (pas son SELECT, son WHERE ni sa limite). */
const SOURCE_OF = (params: unknown): string => {
  const { columns: _c, where: _w, max_rows: _m, ...rest } = (params ?? {}) as Record<string, unknown>;
  return JSON.stringify(rest);
};

/** Colonnes du fichier qu'un lecteur lit, avant son choix d'attributs ; relues quand le fichier change. */
function useFileColumns(node: string): { fields: HelperField[] | null; error: string } {
  const source = useEditor((s) => SOURCE_OF(s.nodes.find((n) => n.id === node)?.data.params));
  const [fields, setFields] = useState<HelperField[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    setError("");
    const { meta, nodes, edges } = useEditor.getState();
    api
      .readerColumns(toFlowDoc(meta, nodes, edges), node)
      .then((list) => live && setFields(list))
      .catch((err: unknown) => {
        if (!live) return;
        setFields([]);
        setError(err instanceof ApiError ? err.message : String(err));
      });
    return () => {
      live = false;
    };
  }, [node, source]);
  return { fields, error };
}

/** Champ « Attributs lus (SELECT) » d'un lecteur : ceux qu'il garde, et le bouton de la requête de lecture. */
export function ReadColumns({ label, value, onOpen }: { label: string; value: string[]; onOpen: () => void }) {
  const text = value.length ? value.join(", ") : t("Tous les attributs");
  return (
    <div className="sql-line">
      <span className="read-columns" role="note" aria-label={label} title={text}>
        {value.length ? t("{{n}} : {{names}}", { n: value.length, names: text }) : text}
      </span>
      <button type="button" className="sql-open" onClick={onOpen} title={t("Choisir les attributs lus et le filtre, dans la requête de lecture du fichier")}>
        {t("Requête de lecture")}
      </button>
    </div>
  );
}

/**
 * Requête de lecture d'un lecteur DuckDB (CSV, Parquet), en surcouche : SELECT (attributs cochés dans la liste),
 * FROM (le fichier), WHERE (condition, avec complétion et vérification), LIMIT. Les colonnes et leurs valeurs viennent
 * du fichier, sans lancer le flux ; « Compter » dit combien d'entités le filtre garde.
 */
export function ReaderQuery({ node, file, columns, where, maxRows, onChange, onClose }: { node: string; file: string; columns: string[]; where: string; maxRows: number; onChange: (patch: { columns?: string[]; where?: string }) => void; onClose: () => void }) {
  const { fields, error } = useFileColumns(node);
  const inserter = useRef<((text: string) => void) | null>(null);
  const [status, setStatus] = useState<ReactNode>(null);
  const [busy, setBusy] = useState(false);
  const check = queryCheck("where");
  const names = (fields ?? []).map((f) => f.name);

  const run = async (task: () => Promise<ReactNode>) => {
    setBusy(true);
    try {
      setStatus(await task());
    } catch (err) {
      setStatus(<span className="error-text">{err instanceof ApiError || err instanceof Error ? err.message : String(err)}</span>);
    } finally {
      setBusy(false);
    }
  };
  const verify = () =>
    run(async () => {
      const r = await check(where);
      if (r.ok) return <span className="ok-text">{t("Expression valide")}</span>;
      return <span className="error-text">{r.line ? t("Ligne {{line}} : {{error}}", { line: r.line, error: r.error ?? "" }) : r.error}</span>;
    });
  const count = () =>
    run(async () => {
      const { meta, nodes, edges } = useEditor.getState();
      const r = await api.readerCount(toFlowDoc(meta, nodes, edges), node);
      const text = where.trim()
        ? t("{{kept}} entités gardées sur {{total}}", { kept: formatNumber(r.kept), total: formatNumber(r.total) })
        : t("{{total}} entités dans le fichier", { total: formatNumber(r.total) });
      return <span className="ok-text">{text}</span>;
    });

  return (
    <Modal title={t("Requête de lecture")} onClose={onClose} className="wide sql-modal">
      <p className="sql-meta muted">
        {t("Dialecte : {{name}}", { name: "DuckDB" })} · {t("Ctrl+Espace : compléter")} · {t("DuckDB ne lit du fichier que les colonnes choisies et celles du filtre.")}
      </p>
      <div className="query-body with-helper">
        <div className="reader-query">
          <section className="rq-part" aria-label="SELECT">
            <div className="rq-head">
              <code className="rq-key">SELECT</code>
              <span className="muted">{t("attributs lus : les cocher dans la liste")}</span>
            </div>
            <code className="rq-text" data-testid="rq-select">
              {columns.length ? columns.map(quoteName).join(", ") : t("* (tous les attributs)")}
            </code>
          </section>
          <section className="rq-part" aria-label="FROM">
            <div className="rq-head">
              <code className="rq-key">FROM</code>
              <code className="rq-text rq-file" title={file}>
                {file || t("(fichier à choisir)")}
              </code>
            </div>
          </section>
          <section className="rq-part rq-where" aria-label="WHERE">
            <div className="rq-head">
              <code className="rq-key">WHERE</code>
              <span className="muted">{t("filtre : seules les entités qui vérifient la condition sont lues ; vide : toutes")}</span>
            </div>
            <Suspense fallback={<textarea aria-label={t("Filtre (WHERE)")} rows={6} value={where} onChange={(e) => onChange({ where: e.target.value })} />}>
              <CodeEditor value={where} language="sql" onChange={(v) => onChange({ where: v })} label={t("Filtre (WHERE)")} dialect="duckdb" schema={{ data: names }} check={check} inserter={inserter} tall />
            </Suspense>
          </section>
          {maxRows > 0 && (
            <section className="rq-part" aria-label="LIMIT">
              <div className="rq-head">
                <code className="rq-key">LIMIT</code>
                <code className="rq-text">{maxRows}</code>
                <span className="muted">{t("(paramètre « Lignes lues au plus »)")}</span>
              </div>
            </section>
          )}
        </div>
        {fields === null ? (
          <p className="muted">
            <Spinner size={11} /> {t("Lecture des colonnes…")}
          </p>
        ) : error ? (
          <p className="error-text">{error}</p>
        ) : (
          <FieldHelper
            source={{ kind: "reader", node, fields }}
            selection={{ chosen: columns, onChange: (next) => onChange({ columns: next }) }}
            onInsert={(text) => (inserter.current ? inserter.current(text) : onChange({ where: where + text }))}
          />
        )}
      </div>
      <div className="modal-actions sql-actions">
        <span className="sql-result" role="status">
          {busy ? <Spinner size={12} /> : status}
        </span>
        <button type="button" onClick={() => void count()} disabled={busy} title={t("Compte les entités du fichier et celles que le filtre garde, sans lancer le flux")}>
          {t("Compter les entités")}
        </button>
        <button type="button" onClick={() => void verify()} disabled={busy}>
          {t("Vérifier")}
        </button>
        <button type="button" className="primary" onClick={onClose}>
          {t("Fermer")}
        </button>
      </div>
    </Modal>
  );
}
