import { Component, lazy, Suspense, useEffect, useMemo, useState, type ReactNode } from "react";
import type { PortInfo, PortRef } from "../api/types";
import { staleOf, useEditor } from "../store/editor";
import { computingNode, followsRun } from "../model/inspection";
import { keyOf, sameTarget, useInspector } from "../store/inspector";
import { DataTable } from "./DataTable";
import { Loading, Spinner } from "./Spinner";
import { formatNumber, t } from "../i18n";

// MapLibre pèse ~1 Mo : chargé seulement quand on ouvre une carte.
const MapView = lazy(() => import("./MapView").then((m) => ({ default: m.MapView })));

/** Un navigateur sans WebGL ne doit pas emporter la table avec lui. */
class MapBoundary extends Component<{ children: ReactNode }, { failed: string | null }> {
  state = { failed: null as string | null };
  static getDerivedStateFromError(e: Error) {
    return { failed: e.message };
  }
  render() {
    return this.state.failed ? <p className="error-text pad">{t("Carte indisponible : {{reason}}", { reason: this.state.failed })}</p> : this.props.children;
  }
}

/** Inspecteur : table et carte du port choisi, filtrées ensemble, calculées côté serveur. */
export function DataPanel() {
  const run = useEditor((s) => s.run);
  const nodes = useEditor((s) => s.nodes);
  const insp = useInspector();
  const { target, info, error, loading, stats, filtered, query, overlays, colorBy, mapVisible, showStats } = insp;
  const { setTarget, patchQuery, toggleStats, toggleOverlay, setColorBy, setMapVisible, exportView } = useInspector.getState();

  // Les ports disponibles : ceux du run affiché, avec leur nombre d'entités.
  const options = useMemo(() => {
    if (!run.id) return [] as { ref: PortRef; count: number }[];
    return Object.entries(run.nodes).flatMap(([node, r]) =>
      Object.entries(r.rows ?? {}).map(([port, count]) => ({ ref: { run: run.id as string, node, port }, count })),
    );
  }, [run.id, run.nodes]);

  // Suit la sélection de l'éditeur (le clic sur un nœud passe aussi par le canevas, même déjà sélectionné) : un nœud
  // sélectionné montre sa première sortie qui a des données, ou dit qu'il n'en a pas.
  const selected = nodes.filter((n) => n.selected && n.type === "etl");
  const selectedId = selected.length === 1 ? selected[0].id : undefined;
  // pas à chaque compte reçu pendant un run : seulement quand le nœud choisi ou la présence de ses données change
  const hasData = !!selectedId && Object.values(run.nodes[selectedId]?.rows ?? {}).some((n) => n > 0);
  useEffect(() => {
    if (selectedId) useInspector.getState().showNode(useEditor.getState().run, selectedId);
  }, [selectedId, run.id, hasData]);

  // Un nouveau run : on garde le même nœud et le même port dans le run courant (`followsRun`).
  const portRows = target ? run.nodes[target.node]?.rows?.[target.port] : undefined;
  useEffect(() => {
    const { run } = useEditor.getState();
    const t = useInspector.getState().target;
    if (run.id && t && followsRun(t, run, selectedId)) void setTarget({ ...t, run: run.id });
  }, [run.id, portRows, setTarget, selectedId]);
  const busyNode = computingNode(run, insp.emptyNode, target);
  // données d'un nœud modifié depuis le run (ou en aval d'un nœud modifié) : elles ne correspondent plus au flux
  const staleTarget = useEditor((s) => !!target && target.run === s.run.id && staleOf(s).has(target.node));

  const [search, setSearch] = useState(query.search);
  useEffect(() => {
    const t = setTimeout(() => search !== query.search && patchQuery({ search }), 350);
    return () => clearTimeout(t);
  }, [search, query.search, patchQuery]);
  const [where, setWhere] = useState(query.where);
  useEffect(() => {
    setSearch(query.search);
    setWhere(query.where);
  }, [target ? keyOf(target) : ""]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!run.id) return <p className="muted pad">{t("Lancez le flux pour inspecter les données de chaque connexion.")}</p>;
  if (run.cache === false)
    return <p className="muted pad">{t("Ce run a tourné sans mise en cache : ses données n'ont pas été gardées. Activer « Cache » (à côté de « Lancer ») puis relancer pour les inspecter.")}</p>;

  const geometry = info?.geometry;
  const hasMap = !!geometry?.map;
  // la carte a sa place dès que le port est cartographiable (en préparation ou en échec : un message à sa place)
  const mapPlace = !!geometry && geometry.map_status !== "none" && mapVisible;
  const numeric = info?.columns.filter((c) => c.category !== "other") ?? [];
  const current = target ? keyOf(target) : "";

  return (
    <div className="data-panel">
      <div className="data-bar">
        <label>
          {t("Port")}{" "}
          <select aria-label={t("Port à inspecter")} value={current} onChange={(e) => void setTarget(options.find((o) => keyOf(o.ref) === e.target.value)?.ref ?? null)}>
            <option value="">{t("— choisir —")}</option>
            {options.map((o) => (
              <option key={keyOf(o.ref)} value={keyOf(o.ref)}>
                {o.ref.node} / {o.ref.port} ({formatNumber(o.count)})
              </option>
            ))}
          </select>
        </label>
        {info && !busyNode && (
          <>
            <input type="search" className="data-search" aria-label={t("Recherche globale")} placeholder={t("Recherche…")} value={search} onChange={(e) => setSearch(e.target.value)} />
            <input
              type="text"
              className="data-where"
              aria-label={t("Clause WHERE")}
              placeholder={t('WHERE SQL, ex. "pop" > 1000')}
              value={where}
              onChange={(e) => setWhere(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && patchQuery({ where })}
              onBlur={() => where !== query.where && patchQuery({ where })}
            />
            <span className="data-count" data-testid="data-count">
              {filtered !== info.total ? `${formatNumber(filtered)} / ` : ""}
              {formatNumber(info.total)} {t("entités")}
            </span>
            <button type="button" aria-pressed={showStats} onClick={() => void toggleStats()}>
              {showStats && !stats && <Spinner size={11} label={t("Calcul des statistiques…")} />} {t("Statistiques")}
            </button>
            <details className="menu">
              <summary>{t("Exporter")}</summary>
              <div className="menu-items">
                {(["csv", "gpkg", "parquet"] as const).map((f) => (
                  <button key={f} type="button" onClick={() => void exportView(f)}>
                    {t("{{format}} (vue filtrée)", { format: { csv: "CSV", gpkg: "GeoPackage", parquet: "Parquet" }[f] })}
                  </button>
                ))}
              </div>
            </details>
            {mapPlace && hasMap && (
              <>
                <label className="inline">
                  <input type="checkbox" checked={mapVisible} onChange={(e) => setMapVisible(e.target.checked)} /> {t("Carte")}
                </label>
                <label>
                  {t("Couleur")}{" "}
                  <select aria-label={t("Colorer par")} value={colorBy ?? ""} onChange={(e) => setColorBy(e.target.value || null)}>
                    <option value="">{t("uniforme")}</option>
                    {numeric.map((c) => (
                      <option key={c.name}>{c.name}</option>
                    ))}
                  </select>
                </label>
                <details className="menu">
                  <summary>{t("Superposer")}{overlays.length ? ` (${overlays.length})` : ""}</summary>
                  <div className="menu-items overlay-list">
                    {options
                      .filter((o) => o.count > 0 && !sameTarget(o.ref, target))
                      .map((o) => (
                        <label key={keyOf(o.ref)}>
                          <input type="checkbox" checked={overlays.some((x) => sameTarget(x, o.ref))} onChange={() => toggleOverlay(o.ref)} /> {o.ref.node} / {o.ref.port}
                        </label>
                      ))}
                  </div>
                </details>
              </>
            )}
          </>
        )}
      </div>
      {error && (
        <p className="error-text pad" role="alert">
          {error}
        </p>
      )}
      {loading && !info && !insp.emptyNode && !busyNode && <Loading text={insp.preparing ? t("Préparation des données…") : t("Chargement des données…")} />}
      {busyNode && <Loading text={t("« {{node}} » en cours de calcul…", { node: busyNode })} />}
      {staleTarget && info && !busyNode && !insp.emptyNode && (
        <p className="stale-banner" role="status" data-testid="data-stale">
          {t("Données du dernier run : ce nœud ou un nœud en amont a changé depuis. Relancer pour les mettre à jour.")}
        </p>
      )}
      {insp.emptyNode && !busyNode && (
        <p className="muted pad" data-testid="data-empty-node">
          {t("« {{node}} » n'a pas de données dans ce run : lancez le flux jusqu'à ce nœud (▶ au-dessus du nœud).", { node: insp.emptyNode })}
        </p>
      )}
      {!target && !error && !insp.emptyNode && <p className="muted pad">{t("Choisissez un port, ou sélectionnez un nœud, ou cliquez une connexion.")}</p>}
      {info && !insp.emptyNode && !busyNode && (
        <div className={`data-body${mapPlace ? " with-map" : ""}`}>
          {loading && <Loading text={t("Chargement des données…")} overlay />}
          <DataTable info={info} />
          {mapPlace && hasMap ? (
            <MapBoundary>
              <Suspense fallback={<Loading text={t("Chargement de la carte…")} />}>
                <MapView info={info} />
              </Suspense>
            </MapBoundary>
          ) : mapPlace ? (
            <MapStatus info={info} />
          ) : geometry?.map_status === "none" ? (
            <p className="muted pad">{t("Carte indisponible : système de coordonnées inconnu.")}</p>
          ) : null}
        </div>
      )}
    </div>
  );
}

/** À la place de la carte : elle se prépare (la table est déjà consultable), ou pourquoi elle n'est pas affichée. */
function MapStatus({ info }: { info: PortInfo }) {
  const g = info.geometry;
  if (g?.map_status === "building")
    return (
      <div className="map-status" role="status" data-testid="map-status">
        <Spinner size={14} />
        <p>{t("Chargement de la carte…")}</p>
      </div>
    );
  return (
    <div className="map-status" role="alert" data-testid="map-status">
      <p>
        <strong>{t("Carte indisponible")}</strong>
        <br />
        {g?.map_error === "memory"
          ? t("Les géométries de ce port sont trop volumineuses pour la mémoire de l'inspecteur. La table reste consultable ; pour les voir sur la carte, simplifiez-les ou filtrez-les en amont (Simplify, Generalizer).")
          : t("La préparation de la carte a échoué : {{detail}}", { detail: g?.map_error ?? "" })}
      </p>
    </div>
  );
}
