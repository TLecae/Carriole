import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { FlowSummary, RunStatus, RunSummary } from "../api/types";
import { enterEditor, newFlowFromHome, openFlowFileFromHome, openFlowFromHome } from "../navigation";
import { pickPath } from "../platform";
import { useEditor } from "../store/editor";
import { useUi } from "../store/ui";
import { AppBar, Brand, Divider, RunsButton, SettingsButton, Spacer, ToolsMenu } from "./AppBar";
import { Loading } from "./Spinner";
import { formatDateTime, t } from "../i18n";

const runLabel = (status: RunStatus): string =>
  ({
    queued: t("en attente"),
    running: t("en cours"),
    done: t("terminé"),
    error: t("échec"),
    cancelled: t("annulé"),
    interrupted: t("interrompu"),
  })[status];

const date = (seconds: number) => formatDateTime(seconds, true);

const matches = (f: FlowSummary, query: string) => {
  const q = query.trim().toLowerCase();
  return !q || [f.name, f.description ?? "", f.owner].some((text) => text.toLowerCase().includes(q));
};

/** Page d'accueil : les flux disponibles, leur dernier run, et les accès aux runs, planifications et connexions. */
export function Home() {
  const flows = useEditor((s) => s.flows);
  const editorName = useEditor((s) => s.meta.name);
  const editorFlowId = useEditor((s) => s.flowId);
  const editorHasWork = useEditor((s) => s.nodes.length > 0);
  const editorDirty = useEditor((s) => s.dirty);
  const files = useUi((u) => u.flowStorage === "files");
  const [query, setQuery] = useState("");
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loaded, setLoaded] = useState(false);

  // Liste des flux et derniers runs, rafraîchis tant que la page est affichée (un run lancé ailleurs apparaît).
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const [, list] = await Promise.all([useEditor.getState().loadFlows(), api.runs("?limit=200")]);
        if (alive) setRuns(list);
      } catch {
        /* l'accueil reste affiché avec ce qu'il a ; le prochain tour réessaiera */
      } finally {
        if (alive) setLoaded(true);
      }
    };
    void load();
    const timer = setInterval(() => void load(), 8000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const lastRun = useMemo(() => {
    const out = new Map<string, RunSummary>();
    for (const r of runs) if (r.flow_id && !out.has(r.flow_id)) out.set(r.flow_id, r); // la liste est du plus récent au plus ancien
    return out;
  }, [runs]);

  const shown = flows.filter((f) => matches(f, query));
  const components = shown.filter((f) => f.component);
  const plain = shown.filter((f) => !f.component);

  const remove = async (f: FlowSummary) => {
    // Flux en fichiers : on retire seulement la ligne de la liste, le fichier n'est jamais supprimé par l'application.
    if (!files && !confirm(t("Supprimer le flux « {{name}} » ?", { name: f.name }))) return;
    await api.deleteFlow(f.id);
    await useEditor.getState().loadFlows();
  };

  const openFile = async () => {
    const path = await pickPath({ mode: "read", title: t("Ouvrir un flux"), extensions: [".json"] });
    if (path) await openFlowFileFromHome(path);
  };

  const card = (f: FlowSummary) => {
    const run = lastRun.get(f.id);
    return (
      <article className="flow-card" key={f.id} data-testid="flow-card">
        <button type="button" className="flow-card-main" onClick={() => void openFlowFromHome(f.id)} aria-label={t("Ouvrir {{name}}", { name: f.name })}>
          <strong>{f.name}</strong>
          <span className="flow-desc">{f.description || t("Pas de description.")}</span>
          <small className="muted" title={f.path}>
            {files ? (f.path ?? "") : t("{{owner}} · modifié {{date}}", { owner: f.owner, date: date(f.updated_at) })}
            {f.node_count !== undefined ? t(" · {{count}} nœud(s)", { count: f.node_count }) : ""}
          </small>
        </button>
        <footer>
          {run ? (
            <span title={run.error ?? undefined}>
              <span className={`run-chip ${run.status}`}>{runLabel(run.status)}</span> <small className="muted">{date(run.created_at)}</small>
            </span>
          ) : (
            <small className="muted">{t("Jamais lancé")}</small>
          )}
          <button
            type="button"
            className="remove"
            aria-label={files ? t("Retirer {{name}} de la liste", { name: f.name }) : t("Supprimer {{name}}", { name: f.name })}
            title={files ? t("Retirer de la liste (le fichier n'est pas supprimé)") : t("Supprimer ce flux")}
            onClick={() => void remove(f)}
          >
            ×
          </button>
        </footer>
      </article>
    );
  };

  return (
    <div className="home">
      <AppBar>
        <Brand />
        <Divider />
        <button className="primary" onClick={newFlowFromHome}>
          {t("+ Nouveau flux")}
        </button>
        {files && <button onClick={() => void openFile()}>{t("Ouvrir un flux…")}</button>}
        <Spacer />
        <RunsButton />
        <ToolsMenu />
        <Divider />
        <SettingsButton />
      </AppBar>

      <main className="home-body">
        <h1>{files ? t("Flux récents") : t("Mes flux")}</h1>
        {editorHasWork && (
          <div className="resume" role="status">
            <span>
              {t("Flux en cours d'édition :")} <strong>{editorName}</strong>
              {editorDirty ? t(" (modifications non enregistrées)") : ""}
            </span>
            <button onClick={() => enterEditor(editorFlowId ? `#flow=${editorFlowId}` : "#new")}>{t("Reprendre")}</button>
          </div>
        )}
        <input type="search" className="home-search" aria-label={t("Rechercher un flux")} placeholder={t("Rechercher un flux…")} value={query} onChange={(e) => setQuery(e.target.value)} />

        {!loaded && <Loading text={t("Chargement des flux…")} />}
        {loaded && flows.length === 0 && (
          <div className="home-empty">
            <p>
              <strong>{files ? t("Aucun flux récent.") : t("Aucun flux enregistré.")}</strong>
            </p>
            <ol>
              <li>{files ? t("Cliquez sur « Ouvrir un flux… » pour reprendre un fichier, ou sur « Nouveau flux ».") : t("Cliquez sur « Nouveau flux ».")}</li>
              <li>{t("Glissez un lecteur depuis la palette (catégorie « Lecture » : CSV, GeoPackage, Shapefile…), puis choisissez un fichier avec « Parcourir… ».")}</li>
              <li>{files ? t("Ajoutez des modificateurs, reliez-les, puis ▶ Lancer. Ctrl+S enregistre le flux dans un fichier.") : t("Ajoutez des modificateurs, reliez-les, puis ▶ Lancer. Ctrl+S enregistre le flux.")}</li>
            </ol>
          </div>
        )}
        {loaded && flows.length > 0 && shown.length === 0 && <p className="muted">{t("Aucun flux ne correspond à « {{query}} ».", { query })}</p>}

        {plain.length > 0 && (
          <section aria-label={t("Flux")}>
            <div className="flow-grid">{plain.map(card)}</div>
          </section>
        )}
        {components.length > 0 && (
          <section aria-label={t("Composants")}>
            <h2>{t("Composants")}</h2>
            <p className="muted">{t("Des flux réutilisables comme des modificateurs, depuis la palette.")}</p>
            <div className="flow-grid">{components.map(card)}</div>
          </section>
        )}
      </main>

    </div>
  );
}
