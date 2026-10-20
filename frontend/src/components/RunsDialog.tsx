import { useEffect, useState } from "react";
import { api, currentUser } from "../api/client";
import type { RunStatus, RunSummary } from "../api/types";
import { useEditor } from "../store/editor";
import { useInspector } from "../store/inspector";
import { useUi } from "../store/ui";
import { formatDateTime, t } from "../i18n";
import { Loading } from "./Spinner";

type Filter = "all" | "active" | "done" | "error" | "interrupted" | "mine";

const FILTERS: Filter[] = ["all", "active", "done", "error", "interrupted", "mine"];

const filterLabel = (f: Filter): string =>
  ({
    all: t("Tous"),
    active: t("En cours"),
    done: t("Terminés"),
    error: t("En échec"),
    interrupted: t("Interrompus"),
    mine: t("Mes runs"),
  })[f];

const statusLabel = (status: RunStatus): string =>
  ({
    queued: t("en attente"),
    running: t("en cours"),
    done: t("terminé"),
    error: t("échec"),
    cancelled: t("annulé"),
    interrupted: t("interrompu"),
  })[status];

const matches = (r: RunSummary, f: Filter, me: string): boolean =>
  f === "all" ||
  (f === "active" && (r.status === "queued" || r.status === "running")) ||
  (f === "done" && r.status === "done") ||
  (f === "error" && (r.status === "error" || r.status === "cancelled")) ||
  (f === "interrupted" && r.status === "interrupted") ||
  (f === "mine" && r.owner === me);

function duration(r: RunSummary): string {
  if (!r.started_at) return "";
  const s = Math.round((r.finished_at ?? Date.now() / 1000) - r.started_at);
  return s < 60 ? t("{{s}} s", { s }) : t("{{m}} min {{s}} s", { m: Math.floor(s / 60), s: s % 60 });
}

/** Liste des runs : en cours, terminés, en échec, interrompus. Ils vivent côté serveur, indépendamment de cette fenêtre. */
export function RunsDialog({ onClose }: { onClose: () => void }) {
  const [runs, setRuns] = useState<RunSummary[] | null>(null); // null : pas encore chargés
  const [filter, setFilter] = useState<Filter>("all");
  const [error, setError] = useState("");
  const { openRun, retryRun } = useEditor.getState();
  const me = currentUser() || "anonyme";

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const list = await api.runs("?limit=200");
        if (alive) setRuns(list);
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : t("Liste indisponible"));
      }
    };
    void load();
    const timer = setInterval(load, 3000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const open = async (r: RunSummary, tab?: "log" | "data") => {
    onClose();
    await openRun(r.id);
    if (tab) useUi.getState().show(tab);
    if (tab === "data") await useInspector.getState().setTarget(null);
  };

  const shown = (runs ?? []).filter((r) => matches(r, filter, me));
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal wide" role="dialog" aria-label={t("Runs")} onMouseDown={(e) => e.stopPropagation()}>
        <h2>{t("Runs")}</h2>
        <div className="tabs" role="tablist">
          {FILTERS.map((f) => (
            <button key={f} role="tab" aria-selected={filter === f} onClick={() => setFilter(f)}>
              {filterLabel(f)}
            </button>
          ))}
        </div>
        {error && <p className="error-text">{error}</p>}
        {runs === null && !error && <Loading text={t("Chargement des runs…")} />}
        {runs !== null && shown.length === 0 && <p className="muted pad">{t("Aucun run.")}</p>}
        <div className="runs-list">
          {shown.map((r) => {
            const resumable = r.status === "interrupted" || r.status === "error" || r.status === "cancelled";
            return (
              <div className="run-row" key={r.id} data-testid="run-row" data-status={r.status}>
                <span className={`run-chip ${r.status}`}>{statusLabel(r.status)}</span>
                <div className="run-main">
                  <strong>{r.flow_name}</strong>
                  {r.options.batch && (
                    <small className="batch-tag" title={t("Lot de {{size}} runs", { size: r.options.batch.size })}>
                      {t("lot {{index}}/{{size}} · {{label}}", r.options.batch)}
                    </small>
                  )}
                  <small>
                    {r.owner} · {formatDateTime(r.created_at)}
                    {duration(r) && ` · ${duration(r)}`}
                    {r.options.target ? t(" · jusqu'à {{target}}", { target: r.options.target }) : ""}
                    {r.options.reuse_from ? t(" · reprise du cache") : ""}
                    {r.purged_at ? t(" · données purgées") : ""}
                  </small>
                  {r.error && <small className="error-text">{r.error}</small>}
                </div>
                <div className="run-actions">
                  <button onClick={() => void open(r)}>{t("Ouvrir")}</button>
                  <button onClick={() => void open(r, "log")}>{t("Journal")}</button>
                  <button onClick={() => void open(r, "data")} disabled={!!r.purged_at || r.status === "queued"} title={r.purged_at ? t("Cache purgé") : ""}>
                    {t("Données")}
                  </button>
                  {(r.status === "queued" || r.status === "running") && (
                    <button className="danger" onClick={() => void api.cancelRun(r.id)}>
                      {t("Annuler")}
                    </button>
                  )}
                  {r.status !== "queued" && r.status !== "running" && (
                    <>
                      <button onClick={() => (onClose(), void retryRun(r.id, false))}>{t("Relancer")}</button>
                      {resumable && (
                        <button onClick={() => (onClose(), void retryRun(r.id, true))} disabled={!!r.purged_at} title={t("Repart du cache : les nœuds terminés ne sont pas recalculés")}>
                          {t("Reprendre")}
                        </button>
                      )}
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
        <div className="modal-actions">
          <button onClick={onClose}>{t("Fermer")}</button>
        </div>
      </div>
    </div>
  );
}
