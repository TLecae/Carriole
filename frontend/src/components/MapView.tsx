import * as maplibregl from "maplibre-gl";
import type { ExpressionSpecification, GeoJSONSource, Map as MlMap, VectorTileSource } from "maplibre-gl";
// Le worker de MapLibre est un module séparé : Vite doit le construire (avec ses dépendances) et nous donner son URL,
// sinon l'URL relative par défaut ne mène nulle part une fois ce code déplacé dans un chunk (« Worker failed to load »).
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, rasterTileTemplate, tileTemplate } from "../api/client";
import type { DataQuery, PortInfo, PortRef, RasterInfo, RasterStyle } from "../api/types";
import { keyOf, sameTarget, useInspector } from "../store/inspector";
import { t } from "../i18n";
import { Spinner } from "./Spinner";

maplibregl.setWorkerUrl(workerUrl);

const PALETTE = ["#1d6fb8", "#d9480f", "#2b8a3e", "#862e9c", "#c2255c", "#e67700"];
const CATEGORY_COLORS = ["#1d6fb8", "#d9480f", "#2b8a3e", "#862e9c", "#c2255c", "#e67700", "#0b7285", "#5f3dc4", "#868e96", "#a61e4d"];
const RAMP = ["#ffffcc", "#a1dab4", "#41b6c4", "#2c7fb8", "#253494"];

/** Fonds de plan raster proposés. Leurs hôtes sont autorisés dans la CSP du backend (`etl_api/security.py`). */
const BASEMAPS = {
  osm: { url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png" },
  satellite: { url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}" },
  ign: {
    url: "https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&FORMAT=image/jpeg&TILEMATRIXSET=PM&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}",
  },
} as const;

const basemapLabel = (kind: keyof typeof BASEMAPS): string =>
  ({ osm: t("Plan OpenStreetMap"), satellite: t("Satellite (Esri)"), ign: t("Photographie aérienne (IGN)") })[kind];

const basemapAttribution = (kind: BasemapKind): string =>
  ({ osm: t("© contributeurs OpenStreetMap"), satellite: t("Imagerie © Esri, Maxar, Earthstar Geographics"), ign: "© IGN" })[kind as "osm"] ?? "";

type BasemapKind = keyof typeof BASEMAPS | "none" | "custom";
interface Basemap {
  kind: BasemapKind;
  url: string; // modèle d'URL du fond « custom »
}

type GeoData = Parameters<GeoJSONSource["setData"]>[0];
const EMPTY = { type: "FeatureCollection", features: [] } as const;
const SELECTION = "sel";

const KINDS: BasemapKind[] = ["osm", "satellite", "ign", "none", "custom"];

function loadBasemap(): Basemap {
  try {
    const saved = JSON.parse(localStorage.getItem("etl.basemap") ?? "") as Partial<Basemap>;
    if (saved.kind && KINDS.includes(saved.kind)) return { kind: saved.kind, url: saved.url ?? "" };
  } catch {
    /* pas de préférence enregistrée */
  }
  return { kind: "osm", url: "" };
}

export function mercatorToLngLat(x: number, y: number): [number, number] {
  return [(x / 20037508.342789244) * 180, (Math.atan(Math.exp(y / 6378137)) * 360) / Math.PI - 90];
}

/** Première couche de données de la carte : le fond de plan se place juste en dessous (donc sous toutes les données).
 * Elle n'a pas de nom fixe : selon le type de géométrie il n'y a que du fill, que de la ligne ou que des points. */
const firstDataLayer = (map: MlMap): string | undefined => map.getStyle().layers.find((l) => /^(data|raster)-/.test(l.id))?.id;
/** Première couche vectorielle : l'image du raster se place juste en dessous (au-dessus du fond de plan). */
const firstVectorLayer = (map: MlMap): string | undefined => map.getStyle().layers.find((l) => l.id.startsWith("data-"))?.id;

const RASTER_LAYER = "raster-0";
const paletteLabel = (name: string): string =>
  ({ gray: t("Gris"), viridis: t("Viridis"), terrain: t("Relief"), ndvi: t("Végétation"), spectral: t("Spectrale") })[name as "gray"] ?? name;

const sourceId = (i: number) => `data-${i}`;
const NO_FILTER: DataQuery = { filters: [], where: "", search: "", sort: [] };
const layerIds = (i: number) => [`${sourceId(i)}-fill`, `${sourceId(i)}-outline`, `${sourceId(i)}-line`, `${sourceId(i)}-circle`];

/** Expression MapLibre de coloration par attribut : dégradé pour un nombre, couleurs distinctes pour un texte. */
async function colorExpression(target: PortRef, info: PortInfo, column: string, query: Parameters<typeof api.dataValues>[2]): Promise<ExpressionSpecification | null> {
  const col = info.columns.find((c) => c.name === column);
  if (!col) return null;
  if (col.category === "numeric") {
    const stats = (await api.dataStats(target)).columns[column];
    const [lo, hi] = [Number(stats?.min), Number(stats?.max)];
    if (!Number.isFinite(lo) || !Number.isFinite(hi) || lo === hi) return null;
    const stops = RAMP.flatMap((c, i) => [lo + ((hi - lo) * i) / (RAMP.length - 1), c]);
    return ["interpolate", ["linear"], ["to-number", ["get", "c"], lo], ...stops] as ExpressionSpecification;
  }
  const values = await api.dataValues(target, column, query, CATEGORY_COLORS.length);
  const pairs = values.flatMap((v, i) => (v.value === null ? [] : [String(v.value), CATEGORY_COLORS[i % CATEGORY_COLORS.length]]));
  if (!pairs.length) return null;
  return ["match", ["to-string", ["get", "c"]], ...pairs, "#9aa5b1"] as ExpressionSpecification;
}

export function MapView({ info }: { info: PortInfo }) {
  const box = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MlMap | null>(null);
  const popup = useRef<maplibregl.Popup | null>(null);
  const [ready, setReady] = useState(false);
  const [tilesLoading, setTilesLoading] = useState(true); // tuiles en cours de chargement (données ou fond de plan)
  const [basemap, setBasemap] = useState<Basemap>(loadBasemap);
  const [config, setConfig] = useState(false);
  const [overlayInfo, setOverlayInfo] = useState<Record<string, PortInfo>>({});
  const [colors, setColors] = useState<ExpressionSpecification | null>(null);
  const { target, query, selectedFid, overlays, colorBy } = useInspector();

  // --- raster : la ligne affichée est la ligne sélectionnée (la première par défaut)
  const isRaster = info.columns.some((c) => c.name === "raster");
  const rasterFid = isRaster ? (selectedFid ?? 0) : null;
  const [rasterInfo, setRasterInfo] = useState<RasterInfo | null>(null);
  const [rasterStyle, setRasterStyle] = useState<RasterStyle | null>(null);
  const [rasterShown, setRasterShown] = useState(true);

  const sources = useMemo(() => {
    if (!target) return [] as { ref: PortRef; info: PortInfo | undefined }[];
    const rest = overlays.filter((o) => !sameTarget(o, target));
    return [
      { ref: target, info },
      ...rest.map((ref) => ({ ref, info: overlayInfo[keyOf(ref)] })),
    ];
  }, [target, info, overlays, overlayInfo]);

  // Les ports superposés : on récupère leur type de géométrie pour choisir le style (et on attend leur carte si elle
  // est en préparation).
  useEffect(() => {
    const timers: ReturnType<typeof setTimeout>[] = [];
    const fetchInfo = (o: PortRef) => void api.dataInfo(o).then((i) => setOverlayInfo((s) => ({ ...s, [keyOf(o)]: i })));
    for (const o of overlays) {
      const known = overlayInfo[keyOf(o)];
      if (!known) fetchInfo(o);
      else if (known.geometry?.map_status === "building") timers.push(setTimeout(() => fetchInfo(o), 1500));
    }
    return () => timers.forEach(clearTimeout);
  }, [overlays, overlayInfo]);

  // --- création de la carte, une fois
  useEffect(() => {
    if (!box.current) return;
    const map = new maplibregl.Map({
      container: box.current,
      style: { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": "#eef1f5" } }] },
      center: [2.4, 46.6],
      zoom: 5,
      attributionControl: { compact: true },
      fadeDuration: 0, // une tuile arrivée s'affiche aussitôt, sans fondu
      refreshExpiredTiles: false, // les tuiles d'un run ne changent pas
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.ScaleControl({}), "bottom-left");
    map.on("load", () => setReady(true));
    map.on("dataloading", () => setTilesLoading(true));
    map.on("idle", () => setTilesLoading(false));
    mapRef.current = map;
    // Le panneau se redimensionne à la souris et passe en pleine page : MapLibre n'écoute que la fenêtre.
    const observer = new ResizeObserver(() => map.resize());
    observer.observe(box.current);
    return () => {
      observer.disconnect();
      map.remove();
      mapRef.current = null;
      setReady(false);
    };
  }, []);

  // --- fond de plan
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    if (map.getLayer("basemap")) map.removeLayer("basemap");
    if (map.getSource("basemap")) map.removeSource("basemap");
    const preset = basemap.kind in BASEMAPS ? BASEMAPS[basemap.kind as keyof typeof BASEMAPS] : null;
    const url = preset ? preset.url : basemap.kind === "custom" ? basemap.url : "";
    if (url) {
      map.addSource("basemap", { type: "raster", tiles: [url], tileSize: 256, maxzoom: 19, attribution: basemapAttribution(basemap.kind) });
      map.addLayer({ id: "basemap", type: "raster", source: "basemap" }, firstDataLayer(map));
    }
    try {
      localStorage.setItem("etl.basemap", JSON.stringify(basemap));
    } catch {
      /* préférence non conservée */
    }
  }, [basemap, ready]);

  // --- coloration par attribut (calculée côté serveur : bornes ou valeurs fréquentes)
  useEffect(() => {
    setColors(null);
    if (target && colorBy) void colorExpression(target, info, colorBy, query).then(setColors);
  }, [target, info, colorBy, query]);

  // --- couches de données : une source de tuiles vectorielles par port affiché. Créée une fois par port ; un autre
  // filtre ou une autre couleur ne fait que changer l'adresse des tuiles (les anciennes restent affichées jusqu'à
  // l'arrivée des nouvelles : pas de carte vide) et la couleur des couches.
  const drawn = useRef<Record<string, { shape: string; url: string }>>({});
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const wanted = new Set<string>();
    sources.forEach(({ ref, info: pi }, i) => {
      if (!pi?.geometry?.map) return;
      const active = i === 0;
      const color = (active && colors ? colors : PALETTE[i % PALETTE.length]) as string;
      const src = sourceId(i);
      const url = tileTemplate(ref, active ? query : NO_FILTER, active && colorBy ? colorBy : null);
      const types = pi.geometry.types.join(" ");
      const shape = `${keyOf(ref)}|${types}|${active && isRaster}`;
      wanted.add(src);
      const known = drawn.current[src];
      if (known?.shape === shape && map.getSource(src)) {
        if (known.url !== url) (map.getSource(src) as VectorTileSource).setTiles([url]);
        for (const id of layerIds(i)) if (map.getLayer(id)) map.setPaintProperty(id, colorProperty(id), color);
        drawn.current[src] = { shape, url };
        return;
      }
      removeData(map, i);
      map.addSource(src, { type: "vector", tiles: [url], minzoom: 0, maxzoom: 15 });
      const polygon = ["==", ["geometry-type"], "Polygon"] as maplibregl.FilterSpecification;
      const layer = { source: src, "source-layer": "data" } as const;
      if (/POLYGON/.test(types) || !types) {
        map.addLayer({ id: `${src}-fill`, type: "fill", ...layer, filter: polygon, paint: { "fill-color": color, "fill-opacity": active && isRaster ? 0.04 : 0.35 } });
        map.addLayer({ id: `${src}-outline`, type: "line", ...layer, filter: polygon, paint: { "line-color": color, "line-width": 1 } });
      }
      if (/LINESTRING/.test(types) || !types) map.addLayer({ id: `${src}-line`, type: "line", ...layer, filter: ["==", ["geometry-type"], "LineString"], paint: { "line-color": color, "line-width": 2 } });
      if (/POINT/.test(types) || !types) {
        map.addLayer({ id: `${src}-circle`, type: "circle", ...layer, filter: ["==", ["geometry-type"], "Point"], paint: { "circle-color": color, "circle-radius": 4, "circle-stroke-color": "#fff", "circle-stroke-width": 1 } });
      }
      drawn.current[src] = { shape, url };
    });
    for (let i = 0; i < 12; i++) {
      if (wanted.has(sourceId(i))) continue;
      removeData(map, i);
      delete drawn.current[sourceId(i)];
    }
    if (map.getLayer("basemap")) map.moveLayer("basemap", firstDataLayer(map));
    for (const id of [`${SELECTION}-fill`, `${SELECTION}-line`, `${SELECTION}-circle`]) if (map.getLayer(id)) map.moveLayer(id); // la sélection reste au-dessus
  }, [ready, sources, query, colorBy, colors, isRaster]);
  useEffect(() => () => void (drawn.current = {}), []); // nouvelle carte : tout est à recréer

  // --- cadrage sur les données quand on change de port
  useEffect(() => {
    const map = mapRef.current;
    const e = info.geometry?.extent_3857;
    if (!map || !ready || !e) return;
    const [w, s] = mercatorToLngLat(e[0], e[1]);
    const [ea, n] = mercatorToLngLat(e[2], e[3]);
    map.fitBounds([[w, s], [ea, n]], { padding: 40, maxZoom: 15, duration: 0 });
  }, [ready, info]);

  // --- sélection : une source à part (géométrie complète de l'entité, pas sa version simplifiée de la tuile)
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    map.addSource(SELECTION, { type: "geojson", data: EMPTY as unknown as GeoData });
    const yellow = "#ffd43b";
    map.addLayer({ id: `${SELECTION}-fill`, type: "fill", source: SELECTION, filter: ["==", ["geometry-type"], "Polygon"], paint: { "fill-color": yellow, "fill-opacity": 0.3 } });
    map.addLayer({ id: `${SELECTION}-line`, type: "line", source: SELECTION, filter: ["!=", ["geometry-type"], "Point"], paint: { "line-color": yellow, "line-width": 4 } });
    map.addLayer({ id: `${SELECTION}-circle`, type: "circle", source: SELECTION, filter: ["==", ["geometry-type"], "Point"], paint: { "circle-color": yellow, "circle-radius": 9, "circle-stroke-color": "#212529", "circle-stroke-width": 2 } });
  }, [ready]);

  // --- raster : description et réglages d'affichage par défaut de la ligne affichée
  useEffect(() => {
    setRasterInfo(null);
    setRasterStyle(null);
    if (!target || rasterFid === null) return;
    let alive = true;
    api
      .rasterInfo(target, rasterFid)
      .then((ri) => {
        if (!alive) return;
        setRasterInfo(ri);
        setRasterStyle({ bands: ri.display.bands, palette: "viridis", range: null });
      })
      .catch(() => undefined); // fichier déplacé ou sans système de coordonnées : pas d'image, l'emprise reste visible
    return () => {
      alive = false;
    };
  }, [target, rasterFid]);

  // --- raster : l'image est une source de tuiles PNG calculées par le serveur
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    if (map.getLayer(RASTER_LAYER)) map.removeLayer(RASTER_LAYER);
    if (map.getSource(RASTER_LAYER)) map.removeSource(RASTER_LAYER);
    if (!target || rasterFid === null || !rasterStyle || !rasterShown) return;
    map.addSource(RASTER_LAYER, { type: "raster", tiles: [rasterTileTemplate(target, rasterFid, rasterStyle)], tileSize: 256, minzoom: 0, maxzoom: 22 });
    map.addLayer({ id: RASTER_LAYER, type: "raster", source: RASTER_LAYER, paint: { "raster-resampling": "nearest" } }, firstVectorLayer(map));
    if (map.getLayer("basemap")) map.moveLayer("basemap", firstDataLayer(map));
  }, [ready, target, rasterFid, rasterStyle, rasterShown, sources]);

  // --- état observable (tests et diagnostic) : ce que la carte a réellement rendu une fois les tuiles chargées
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const report = () => {
      const ids = ["fill", "line", "circle"].map((k) => `data-0-${k}`).filter((id) => map.getLayer(id));
      box.current?.setAttribute("data-features", String(ids.length ? map.queryRenderedFeatures({ layers: ids }).length : 0));
    };
    // l'ordre des couches, à chaque changement de style : le fond de plan doit être sous toutes les données
    const order = () => box.current?.setAttribute("data-layer-order", map.getStyle().layers.map((l) => l.id).join(","));
    map.on("idle", report);
    map.on("idle", order);
    map.on("styledata", order);
    order();
    return () => {
      map.off("idle", report);
      map.off("idle", order);
      map.off("styledata", order);
    };
  }, [ready]);
  useEffect(() => {
    box.current?.setAttribute("data-color", colorBy ?? "");
  }, [colorBy]);

  // --- entité sélectionnée : surlignée, et la carte la rejoint si elle est hors champ
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const selection = map.getSource(SELECTION) as GeoJSONSource | undefined;
    popup.current?.remove();
    if (selectedFid === null || !target) {
      selection?.setData(EMPTY as unknown as GeoData);
      return;
    }
    let alive = true;
    void api.dataFeature(target, selectedFid).then((f) => {
      if (!alive) return;
      selection?.setData(f.geometry ? ({ type: "Feature", properties: {}, geometry: f.geometry } as unknown as GeoData) : (EMPTY as unknown as GeoData));
      if (!f.bbox) return;
      const [w, s, e, n] = f.bbox;
      const center: [number, number] = [(w + e) / 2, (s + n) / 2];
      if (w === e && s === n) {
        if (!map.getBounds().contains(center)) map.easeTo({ center, zoom: Math.max(map.getZoom(), 12) });
      } else {
        const b = map.getBounds();
        if (!(b.contains([w, s]) && b.contains([e, n]))) map.fitBounds([[w, s], [e, n]], { padding: 60, maxZoom: 16 });
      }
    });
    return () => {
      alive = false;
    };
  }, [selectedFid, target, ready]);

  // --- clic : sélectionne l'entité (la table défile jusqu'à elle) et affiche ses attributs
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const onClick = (ev: maplibregl.MapMouseEvent) => {
      const ids = ["fill", "line", "circle"].map((k) => `data-0-${k}`).filter((id) => map.getLayer(id));
      const hit = ids.length ? map.queryRenderedFeatures(ev.point, { layers: ids })[0] : undefined;
      const fid = typeof hit?.id === "number" ? hit.id : null;
      void useInspector.getState().select(fid, "map");
      popup.current?.remove();
      if (target && rasterFid !== null) {
        void showPixel(map, target, rasterFid, ev.lngLat, popup);
        return;
      }
      if (fid === null || !target) return;
      void api.dataFeature(target, fid).then((f) => {
        const el = document.createElement("div");
        el.className = "feature-popup";
        const table = document.createElement("table");
        for (const [k, v] of Object.entries(f.attributes)) {
          const tr = table.insertRow();
          tr.insertCell().textContent = k;
          tr.insertCell().textContent = v === null ? "" : String(v);
        }
        el.append(table);
        popup.current = new maplibregl.Popup({ maxWidth: "320px" }).setLngLat(ev.lngLat).setDOMContent(el).addTo(map);
      });
    };
    map.on("click", onClick);
    const pointer = () => (map.getCanvas().style.cursor = "pointer");
    const reset = () => (map.getCanvas().style.cursor = "");
    for (const k of ["fill", "line", "circle"]) {
      map.on("mouseenter", `data-0-${k}`, pointer);
      map.on("mouseleave", `data-0-${k}`, reset);
    }
    return () => {
      map.off("click", onClick);
    };
  }, [ready, target, sources, rasterFid]);

  return (
    <div className="map-wrap">
      <div className="map" ref={box} data-testid="map" />
      {(!ready || tilesLoading) && (
        <div className="map-loading" data-testid="map-loading">
          <Spinner size={12} /> {t("Chargement de la carte…")}
        </div>
      )}
      <div className="map-config">
        <div className="segmented" role="group" aria-label={t("Fond de plan")}>
          <button type="button" aria-pressed={basemap.kind === "osm"} onClick={() => setBasemap({ ...basemap, kind: "osm" })}>
            {t("Plan")}
          </button>
          <button type="button" aria-pressed={basemap.kind === "satellite"} onClick={() => setBasemap({ ...basemap, kind: "satellite" })}>
            {t("Satellite")}
          </button>
        </div>
        <button type="button" aria-label={t("Réglages du fond de plan")} aria-expanded={config} title={t("Réglages du fond de plan")} onClick={() => setConfig(!config)}>
          ⚙
        </button>
      </div>
      {isRaster && rasterInfo && rasterStyle && (
        <RasterControls info={rasterInfo} style={rasterStyle} shown={rasterShown} onShown={setRasterShown} onStyle={setRasterStyle} />
      )}
      {config && (
        <div className="map-config-panel">
          <label>
            {t("Fond de plan")}
            <select value={basemap.kind} onChange={(e) => setBasemap({ ...basemap, kind: e.target.value as BasemapKind })}>
              {(Object.keys(BASEMAPS) as (keyof typeof BASEMAPS)[]).map((kind) => (
                <option key={kind} value={kind}>
                  {basemapLabel(kind)}
                </option>
              ))}
              <option value="none">{t("Aucun (hors ligne)")}</option>
              <option value="custom">{t("Adresse personnalisée")}</option>
            </select>
          </label>
          {basemap.kind === "custom" && (
            <label>
              {t("Modèle d'URL")}
              <input type="text" value={basemap.url} placeholder="https://…/{z}/{x}/{y}.png" onChange={(e) => setBasemap({ ...basemap, url: e.target.value })} />
              <small className="muted">{t("Le serveur de tuiles doit être autorisé dans la politique CSP (voir le guide d'installation).")}</small>
            </label>
          )}
          {basemap.kind !== "none" && basemap.kind !== "custom" && <small className="muted">{t("Les tuiles sont chargées depuis internet par votre navigateur.")}</small>}
        </div>
      )}
    </div>
  );
}

/** Propriété de couleur d'une couche de données, selon son type. */
const colorProperty = (layer: string) => (layer.endsWith("-fill") ? "fill-color" : layer.endsWith("-circle") ? "circle-color" : "line-color");

/** Retire la source de données `i` et ses couches. */
function removeData(map: MlMap, i: number): void {
  for (const id of [...layerIds(i), `${sourceId(i)}-hl-circle`, `${sourceId(i)}-hl-line`]) if (map.getLayer(id)) map.removeLayer(id);
  if (map.getSource(sourceId(i))) map.removeSource(sourceId(i));
}

/** Clic sur un port raster : valeur de chaque bande au pixel visé, dans une bulle. */
async function showPixel(map: MlMap, target: PortRef, fid: number, at: maplibregl.LngLat, popup: { current: maplibregl.Popup | null }): Promise<void> {
  try {
    const px = await api.rasterPixel(target, fid, at.lng, at.lat);
    const el = document.createElement("div");
    el.className = "feature-popup";
    if (!px.inside) el.textContent = t("En dehors du raster");
    else {
      const table = document.createElement("table");
      for (const [k, v] of Object.entries(px.values)) {
        const tr = table.insertRow();
        tr.insertCell().textContent = k;
        tr.insertCell().textContent = v === null ? t("sans valeur") : String(Number(v.toPrecision(7)));
      }
      const caption = document.createElement("small");
      caption.className = "muted";
      caption.textContent = t("Pixel : ligne {{row}}, colonne {{col}}", { row: px.row, col: px.col });
      el.append(caption, table);
    }
    popup.current?.remove();
    popup.current = new maplibregl.Popup({ maxWidth: "320px" }).setLngLat(at).setDOMContent(el).addTo(map);
  } catch {
    /* raster illisible : pas de bulle */
  }
}

type RasterControlsProps = {
  info: RasterInfo;
  style: RasterStyle;
  shown: boolean;
  onShown: (v: boolean) => void;
  onStyle: (s: RasterStyle) => void;
};

/** Réglages d'affichage d'un raster : bandes (une avec palette, ou trois en composition), palette et étendue. */
function RasterControls({ info, style, shown, onShown, onStyle }: RasterControlsProps) {
  const rgb = style.bands.length === 3;
  const bandOptions = info.bands.map((b) => (
    <option key={b.index} value={b.index}>
      {b.name}
    </option>
  ));
  const auto = info.display.ranges[String(style.bands[0])];
  const low = style.range?.[0] ?? auto?.[0];
  const high = style.range?.[1] ?? auto?.[1];
  const setRange = (a: string, b: string) => {
    const [lo, hi] = [parseFloat(a), parseFloat(b)];
    onStyle({ ...style, range: Number.isFinite(lo) && Number.isFinite(hi) && hi > lo ? [lo, hi] : null });
  };
  const shownValue = (v: number | undefined) => (v === undefined ? "" : Number(v.toPrecision(6)));
  return (
    <div className="map-raster" data-testid="raster-controls">
      <label className="inline">
        <input type="checkbox" checked={shown} onChange={(e) => onShown(e.target.checked)} /> {t("Afficher le raster")}
      </label>
      {shown && info.count >= 3 && (
        <div className="segmented" role="group" aria-label={t("Mode d'affichage")}>
          <button type="button" aria-pressed={!rgb} onClick={() => onStyle({ ...style, bands: [style.bands[0]], range: null })}>
            {t("Une bande")}
          </button>
          <button type="button" aria-pressed={rgb} onClick={() => onStyle({ ...style, bands: [1, 2, 3], range: null })}>
            {t("Composition RVB")}
          </button>
        </div>
      )}
      {shown &&
        rgb &&
        [t("Rouge"), t("Vert"), t("Bleu")].map((label, k) => (
          <label key={label}>
            {label}
            <select value={style.bands[k]} onChange={(e) => onStyle({ ...style, bands: style.bands.map((b, i) => (i === k ? Number(e.target.value) : b)), range: null })}>
              {bandOptions}
            </select>
          </label>
        ))}
      {shown && !rgb && (
        <>
          <label>
            {t("Bande")}
            <select value={style.bands[0]} onChange={(e) => onStyle({ ...style, bands: [Number(e.target.value)], range: null })}>
              {bandOptions}
            </select>
          </label>
          <label>
            {t("Palette")}
            <select value={style.palette} onChange={(e) => onStyle({ ...style, palette: e.target.value })}>
              {info.palettes.map((p) => (
                <option key={p} value={p}>
                  {paletteLabel(p)}
                </option>
              ))}
            </select>
          </label>
          <div className="range-row">
            <label>
              {t("Min")}
              <input type="number" value={shownValue(low)} onChange={(e) => setRange(e.target.value, String(high))} />
            </label>
            <label>
              {t("Max")}
              <input type="number" value={shownValue(high)} onChange={(e) => setRange(String(low), e.target.value)} />
            </label>
          </div>
          {style.range && (
            <button type="button" onClick={() => onStyle({ ...style, range: null })}>
              {t("Étirement automatique")}
            </button>
          )}
        </>
      )}
      <small className="muted">{t("Cliquer sur la carte donne la valeur du pixel.")}</small>
    </div>
  );
}
