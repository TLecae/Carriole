import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { ComponentVersions } from "../api/types";
import { useEditor } from "../store/editor";
import { formatDateTime, t } from "../i18n";
import { Spinner } from "./Spinner";

const date = (ts: number) => formatDateTime(ts, true);

function useVersions(name: string, refreshKey: unknown = 0): ComponentVersions | null {
  const [data, setData] = useState<ComponentVersions | null>(null);
  useEffect(() => {
    let live = true;
    setData(null);
    api
      .componentVersions(name)
      .then((d) => live && setData(d))
      .catch(() => live && setData({ versions: [], draft_changed: false }));
    return () => {
      live = false;
    };
  }, [name, refreshKey]);
  return data;
}

/** Nœud composant : choix de la version épinglée (ou « dernière publiée »). */
export function VersionPicker({ id, name, current, latest }: { id: string; name: string; current: number | null; latest: number | null }) {
  const data = useVersions(name);
  const set = (v: number | null) => useEditor.getState().updateNodeData(id, { componentVersion: v });
  if (!data) return <Spinner size={12} label={t("Chargement des versions…")} />;
  if (data.versions.length === 0) {
    return <p className="muted">{t("Aucune version publiée : le composant utilisé est le flux lui-même (il change dès qu'on l'enregistre).")}</p>;
  }
  const outdated = current !== null && latest !== null && current < latest;
  return (
    <div className="version-picker">
      <label className="field">
        <span className="field-label">{t("Version du composant")}</span>
        <select aria-label={t("Version du composant")} value={current === null ? "" : String(current)} onChange={(e) => set(e.target.value === "" ? null : Number(e.target.value))}>
          <option value="">{t("Dernière publiée (v{{latest}})", { latest })}</option>
          {data.versions.map((v) => (
            <option key={v.version} value={v.version}>
              v{v.version}
              {v.note ? ` — ${v.note}` : ""} ({date(v.created_at)})
            </option>
          ))}
        </select>
      </label>
      {outdated && (
        <p className="warn-text">
          {t("Une version plus récente existe (v{{latest}}).", { latest })}{" "}
          <button type="button" className="link" onClick={() => set(latest)}>
            {t("Passer à la v{{latest}}", { latest })}
          </button>
        </p>
      )}
    </div>
  );
}

/** Flux déclaré comme composant : versions publiées et bouton de publication. */
export function PublishVersions({ name }: { name: string }) {
  const flowId = useEditor((s) => s.flowId);
  const dirty = useEditor((s) => s.dirty);
  const [refresh, setRefresh] = useState(0);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const data = useVersions(name, refresh);

  const publish = useCallback(async () => {
    const { save, loadPalette, setNotice } = useEditor.getState();
    setBusy(true);
    try {
      if (dirty && !(await save())) return;
      const v = await api.publishComponent(name, note);
      setNote("");
      setRefresh((n) => n + 1);
      await loadPalette();
      setNotice({ kind: "info", text: t("Version {{version}} publiée", { version: v.version }) });
    } catch (e) {
      setNotice({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }, [dirty, name, note]);

  if (!flowId) return <p className="muted">{t("Enregistrer le flux (Ctrl+S) pour pouvoir publier des versions.")}</p>;
  return (
    <div className="component-versions">
      <h4>{t("Versions publiées")}</h4>
      <p className="muted">
        {t("Une version est un instantané du composant enregistré. Les nœuds qui l'utilisent peuvent l'épingler ; sinon ils suivent la dernière.")}
      </p>
      {data && data.versions.length === 0 && <p className="muted">{t("Aucune version : les flux utilisent le composant tel qu'il est enregistré.")}</p>}
      <ul className="version-list">
        {data?.versions.map((v) => (
          <li key={v.version}>
            <strong>v{v.version}</strong> {date(v.created_at)} · {v.author}
            {v.note ? ` — ${v.note}` : ""}
          </li>
        ))}
      </ul>
      {data && data.versions.length > 0 && data.draft_changed && !dirty && <p className="warn-text">{t("Le composant a changé depuis la v{{version}}.", { version: data.versions[0].version })}</p>}
      <label className="field">
        <span className="field-label">{t("Note de version")}</span>
        <input type="text" aria-label={t("Note de version")} value={note} placeholder={t("Ce qui change")} onChange={(e) => setNote(e.target.value)} />
      </label>
      <button type="button" className="add" disabled={busy} onClick={() => void publish()}>
        {t("Publier une version")}
      </button>
    </div>
  );
}
