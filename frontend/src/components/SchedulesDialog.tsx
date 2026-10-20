import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { FlowSummary, PublishedParam, ScheduleInfo, ScheduleInput } from "../api/types";
import { useEditor } from "../store/editor";
import { formatDateTime, t } from "../i18n";

const presets = (): { label: string; cron: string }[] => [
  { label: t("Toutes les heures"), cron: "0 * * * *" },
  { label: t("Chaque nuit à 2 h"), cron: "0 2 * * *" },
  { label: t("Jours ouvrés à 6 h 30"), cron: "30 6 * * 1-5" },
  { label: t("Le lundi à 7 h"), cron: "0 7 * * 1" },
  { label: t("Le 1er du mois à 3 h"), cron: "0 3 1 * *" },
];

const EMPTY: ScheduleInput = { name: "", flow_id: "", cron: "0 2 * * *", params: {}, cache: false, notify_email: null, allow_overlap: false, enabled: true };

const when = (ts: number | null): string => (ts ? formatDateTime(ts, true) : "—");

/** Planification : lancer un flux enregistré selon une expression cron (côté serveur, sans navigateur ouvert). */
export function SchedulesDialog({ onClose }: { onClose: () => void }) {
  const [list, setList] = useState<ScheduleInfo[]>([]);
  const [flows, setFlows] = useState<FlowSummary[]>([]);
  const [editing, setEditing] = useState<string | null>(null); // id en cours de modification, « new » pour une création
  const [form, setForm] = useState<ScheduleInput>(EMPTY);
  const [declared, setDeclared] = useState<PublishedParam[]>([]);
  const [preview, setPreview] = useState<{ ok: boolean; text: string }>({ ok: true, text: "" });
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const openRun = useEditor.getState().openRun;

  const load = useCallback(async () => {
    try {
      setList(await api.schedules());
    } catch (e) {
      setMessage({ kind: "error", text: e instanceof Error ? e.message : t("Liste indisponible") });
    }
  }, []);

  useEffect(() => {
    void load();
    void api.flows().then((f) => setFlows(f.filter((x) => !x.component))).catch(() => undefined);
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [load]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Prochaines échéances de l'expression saisie (calculées par le serveur : même fuseau, mêmes règles)
  useEffect(() => {
    if (!editing) return;
    const handle = setTimeout(() => {
      api
        .previewCron(form.cron, 3)
        .then((r) => setPreview({ ok: true, text: t("Prochaines échéances : {{dates}}", { dates: r.next.map(when).join(" · ") }) }))
        .catch((e) => setPreview({ ok: false, text: e instanceof Error ? e.message : t("Expression invalide") }));
    }, 300);
    return () => clearTimeout(handle);
  }, [form.cron, editing]);

  // Paramètres publiés du flux choisi : un champ chacun, avec sa valeur par défaut
  useEffect(() => {
    if (!form.flow_id) {
      setDeclared([]);
      return;
    }
    let alive = true;
    void api.flow(form.flow_id).then((r) => {
      if (!alive) return;
      setDeclared(r.flow.published_params);
      setForm((f) => ({ ...f, params: Object.fromEntries(r.flow.published_params.map((p) => [p.name, f.params[p.name] ?? p.default])) }));
    });
    return () => {
      alive = false;
    };
  }, [form.flow_id]);

  const start = (s?: ScheduleInfo) => {
    setMessage(null);
    setEditing(s ? s.id : "new");
    setForm(
      s
        ? { name: s.name, flow_id: s.flow_id, cron: s.cron, params: s.params, cache: !!s.options.cache, notify_email: s.options.notify_email ?? null, allow_overlap: !!s.options.allow_overlap, enabled: s.enabled }
        : EMPTY,
    );
  };
  const set = <K extends keyof ScheduleInput>(key: K, value: ScheduleInput[K]) => setForm((f) => ({ ...f, [key]: value }));

  const run = async (action: () => Promise<unknown>, success: string) => {
    try {
      await action();
      setMessage({ kind: "ok", text: success });
      await load();
    } catch (e) {
      setMessage({ kind: "error", text: e instanceof Error ? e.message : t("Échec") });
    }
  };
  const save = () =>
    run(async () => {
      if (editing === "new") await api.createSchedule(form);
      else await api.updateSchedule(editing!, form);
      setEditing(null);
    }, t("Planification enregistrée"));

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal wide" role="dialog" aria-label={t("Planification")} onMouseDown={(e) => e.stopPropagation()}>
        <h2>{t("Planification")}</h2>
        <p className="muted">{t("Les flux planifiés sont lancés par les workers, même sans navigateur ouvert. Heure du serveur.")}</p>
        <div className="runs-list">
          {list.length === 0 && <p className="muted pad">{t("Aucune planification.")}</p>}
          {list.map((s) => (
            <div className="run-row" key={s.id} data-testid="schedule-row" data-enabled={s.enabled}>
              <span className={`run-chip ${s.enabled ? "done" : "cancelled"}`}>{s.enabled ? t("active") : t("suspendue")}</span>
              <div className="run-main">
                <strong>{s.name}</strong>
                <small>
                  {s.flow_name ?? t("flux supprimé")} · <code>{s.cron}</code> · {t("prochaine : {{date}}", { date: when(s.next_run_at) })} · {t("dernière : {{date}}", { date: when(s.last_run_at) })}
                </small>
                {s.last_error && <small className="error-text">{s.last_error}</small>}
                {s.warnings?.map((w) => (
                  <small key={w} className="muted" role="note">
                    ⚠ {w}
                  </small>
                ))}
              </div>
              <div className="run-actions">
                <button onClick={() => start(s)}>{t("Modifier")}</button>
                <button onClick={() => void run(() => api.runSchedule(s.id), t("Run lancé"))}>{t("Lancer maintenant")}</button>
                {s.last_run_id && (
                  <button
                    onClick={() => {
                      onClose();
                      void openRun(s.last_run_id!);
                    }}
                  >
                    {t("Dernier run")}
                  </button>
                )}
                <button className="danger" onClick={() => confirm(t("Supprimer « {{name}} » ?", { name: s.name })) && void run(() => api.deleteSchedule(s.id), t("Planification supprimée"))}>
                  {t("Supprimer")}
                </button>
              </div>
            </div>
          ))}
        </div>
        {editing === null ? (
          <div className="modal-actions">
            <button className="primary" onClick={() => start()}>
              {t("+ Nouvelle planification")}
            </button>
          </div>
        ) : (
          <div className="conn-form" aria-label={t("Formulaire de planification")}>
            <label className="field">
              <span className="field-label">{t("Nom")}</span>
              <input aria-label={t("Nom de la planification")} value={form.name} onChange={(e) => set("name", e.target.value)} />
            </label>
            <label className="field">
              <span className="field-label">{t("Flux (enregistré)")}</span>
              <select aria-label={t("Flux à lancer")} value={form.flow_id} onChange={(e) => set("flow_id", e.target.value)}>
                <option value="">{t("— choisir —")}</option>
                {flows.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="field-label">{t("Expression cron (minute heure jour mois jour-de-semaine)")}</span>
              <input aria-label={t("Expression cron")} value={form.cron} onChange={(e) => set("cron", e.target.value)} />
              <span className="field-hint">
                {presets().map((p) => (
                  <button type="button" key={p.cron} className="link" onClick={() => set("cron", p.cron)}>
                    {p.label}
                  </button>
                ))}
              </span>
              <span className={preview.ok ? "ok-text" : "error-text"} role="status" data-testid="cron-preview">
                {preview.text}
              </span>
            </label>
            {declared.map((p) => (
              <label className="field" key={p.name}>
                <span className="field-label">{p.label || p.name}</span>
                <input aria-label={t("Paramètre {{name}}", { name: p.name })} value={form.params[p.name] ?? ""} onChange={(e) => set("params", { ...form.params, [p.name]: e.target.value })} />
              </label>
            ))}
            <label className="field">
              <span className="field-label">{t("E-mail de fin de run (facultatif)")}</span>
              <input type="email" aria-label={t("E-mail de la planification")} value={form.notify_email ?? ""} onChange={(e) => set("notify_email", e.target.value || null)} />
            </label>
            <label className="field inline">
              <input type="checkbox" checked={form.cache} onChange={(e) => set("cache", e.target.checked)} />
              <span className="field-label">{t("Garder les données de chaque nœud (cache)")}</span>
            </label>
            <label className="field inline">
              <input type="checkbox" checked={form.allow_overlap} onChange={(e) => set("allow_overlap", e.target.checked)} />
              <span className="field-label">{t("Lancer même si le run précédent n'est pas terminé")}</span>
            </label>
            <label className="field inline">
              <input type="checkbox" checked={form.enabled} onChange={(e) => set("enabled", e.target.checked)} />
              <span className="field-label">{t("Active")}</span>
            </label>
            <div className="modal-actions">
              <button onClick={() => setEditing(null)}>{t("Annuler")}</button>
              <button className="primary" disabled={!form.name.trim() || !form.flow_id || !preview.ok} onClick={() => void save()}>
                {t("Enregistrer")}
              </button>
            </div>
          </div>
        )}
        {message && (
          <p role={message.kind === "error" ? "alert" : "status"} className={message.kind === "error" ? "error-text" : "ok-text"}>
            {message.text}
          </p>
        )}
        <div className="modal-actions">
          <button onClick={onClose}>{t("Fermer")}</button>
        </div>
      </div>
    </div>
  );
}
