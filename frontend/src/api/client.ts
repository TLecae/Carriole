import type {
  AppSettings,
  AppSettingsValues,
  Analysis,
  ComponentVersion,
  ComponentVersions,
  ConnectionInfo,
  ConnectionInput,
  ConnectionTest,
  PgTableInfo,
  DataQuery,
  FeatureInfo,
  FileListing,
  FlowDoc,
  FlowRecord,
  FlowSummary,
  PortInfo,
  PortRef,
  PortStats,
  RowsPage,
  RunDetail,
  RunEvent,
  RunSummary,
  ScheduleInfo,
  ScheduleInput,
  AssistantConfig,
  AssistantEvent,
  AssistantProvider,
  ChatCard,
  ChatRecord,
  Preferences,
  RasterInfo,
  RasterPixel,
  RasterStyle,
  SendBody,
  ModifierInfo,
} from "./types";
import { language, t } from "../i18n";
import { reportOffline, reportOnline } from "./connection";

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(describe(detail) || t("Erreur {{status}}", { status }));
  }
}

function describe(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "message" in detail) return String((detail as { message: unknown }).message);
  return "";
}

export function currentEmail(): string {
  try {
    return localStorage.getItem("etl.email") ?? "";
  } catch {
    return "";
  }
}

export function setCurrentEmail(email: string): void {
  try {
    localStorage.setItem("etl.email", email);
  } catch {
    /* préférence non conservée */
  }
}

export function currentUser(): string {
  try {
    return localStorage.getItem("etl.user") ?? "";
  } catch {
    return "";
  }
}

export function setCurrentUser(name: string): void {
  try {
    localStorage.setItem("etl.user", name);
  } catch {
    /* stockage indisponible : l'utilisateur reste anonyme */
  }
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json", "Accept-Language": language() };
  const user = currentUser();
  if (user) headers["X-ETL-User"] = user;
  let res: Response;
  try {
    res = await fetch(`/api${path}`, { ...init, headers: { ...headers, ...(init.headers as Record<string, string>) } });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    reportOffline(); // pas de réponse : moteur arrêté ou en cours de relance (ApiError de statut 0)
    throw new ApiError(0, t("Le moteur ne répond pas (arrêté ou en cours de relance) : réessayez dans un instant."));
  }
  reportOnline();
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  const body = text ? JSON.parse(text) : undefined;
  if (!res.ok) throw new ApiError(res.status, body?.detail ?? body);
  return body as T;
}

const json = (body: unknown): RequestInit => ({ body: JSON.stringify(body) });

export const api = {
  modifiers: () => call<{ modifiers: ModifierInfo[]; errors: Record<string, string> }>("/modifiers"),
  flows: () => call<FlowSummary[]>("/flows"),
  componentVersions: (name: string) => call<ComponentVersions>(`/components/${encodeURIComponent(name)}/versions`),
  publishComponent: (name: string, note: string) =>
    call<ComponentVersion>(`/components/${encodeURIComponent(name)}/versions`, { method: "POST", ...json({ note }) }),
  flow: (id: string) => call<FlowRecord>(`/flows/${id}`),
  version: () => call<{ version: string; flow_storage?: "files" | "db" }>("/version"),
  preferences: () => call<Preferences>("/preferences"),
  appSettings: () => call<AppSettings>("/settings"),
  patchAppSettings: (patch: Partial<{ [K in keyof AppSettingsValues]: AppSettingsValues[K] | null }>) =>
    call<AppSettings>("/settings", { method: "PATCH", ...json(patch) }),
  setLanguage: (language: string) => call<Preferences>("/preferences/language", { method: "PUT", ...json({ language }) }),
  createFlow: (flow: FlowDoc, path?: string) => call<FlowRecord>("/flows", { method: "POST", ...json({ flow, path }) }),
  openFlowFile: (path: string) => call<FlowRecord>("/flows/open", { method: "POST", ...json({ path }) }),
  saveFlow: (id: string, flow: FlowDoc, revision: number | string | null) =>
    call<FlowRecord>(`/flows/${id}`, { method: "PUT", ...json({ flow, revision }) }),
  deleteFlow: (id: string) => call<void>(`/flows/${id}`, { method: "DELETE" }),
  analyze: (flow: FlowDoc, params: Record<string, string> = {}) =>
    call<Analysis>("/analyze", { method: "POST", ...json({ flow, params }) }),
  createRun: (body: {
    flow?: FlowDoc;
    flow_id?: string | null;
    params?: Record<string, string>;
    cache?: boolean;
    target?: string | null;
    reuse_from?: string | null;
    notify_email?: string | null;
  }) => call<RunSummary>("/runs", { method: "POST", ...json(body) }),
  createBatch: (body: {
    flow?: FlowDoc;
    flow_id?: string | null;
    param: string;
    pattern?: string;
    values?: string[];
    params?: Record<string, string>;
    cache?: boolean;
    notify_email?: string | null;
  }) => call<{ batch: string; runs: RunSummary[] }>("/runs/batch", { method: "POST", ...json(body) }),
  previewBatch: (body: { flow?: FlowDoc; flow_id?: string | null; param: string; pattern?: string; values?: string[] }) =>
    call<{ values: string[] }>("/runs/batch", { method: "POST", ...json({ ...body, dry_run: true }) }),
  retryRun: (id: string, resume: boolean) => call<RunSummary>(`/runs/${id}/retry`, { method: "POST", ...json({ resume }) }),
  runs: (query = "") => call<RunSummary[]>(`/runs${query}`),
  run: (id: string) => call<RunDetail>(`/runs/${id}`),
  cancelRun: (id: string) => call<RunSummary>(`/runs/${id}/cancel`, { method: "POST" }),
  runEvents: (id: string, after = 0) => call<RunEvent[]>(`/runs/${id}/events?stream=false&after=${after}`),

  // fichiers du serveur
  files: (path: string, mode: "read" | "write", recognizedOnly: boolean, extensions: string[] = []) =>
    call<FileListing>(
      `/files?path=${encodeURIComponent(path)}&mode=${mode}&recognized=${recognizedOnly}&extensions=${encodeURIComponent(extensions.join(","))}`,
    ),

  // connexions aux bases
  connections: () => call<ConnectionInfo[]>("/connections"),
  saveConnection: (name: string, body: ConnectionInput) =>
    call<ConnectionInfo>(`/connections/${encodeURIComponent(name)}`, { method: "PUT", ...json(body) }),
  deleteConnection: (name: string) => call<void>(`/connections/${encodeURIComponent(name)}`, { method: "DELETE" }),
  testConnection: (name: string) => call<ConnectionTest>(`/connections/${encodeURIComponent(name)}/test`, { method: "POST" }),
  connectionTables: (name: string) => call<PgTableInfo[]>(`/connections/${encodeURIComponent(name)}/tables`),

  // planifications
  schedules: () => call<ScheduleInfo[]>("/schedules"),
  createSchedule: (body: ScheduleInput) => call<ScheduleInfo>("/schedules", { method: "POST", ...json(body) }),
  updateSchedule: (id: string, body: ScheduleInput) => call<ScheduleInfo>(`/schedules/${id}`, { method: "PUT", ...json(body) }),
  deleteSchedule: (id: string) => call<void>(`/schedules/${id}`, { method: "DELETE" }),
  runSchedule: (id: string) => call<RunSummary>(`/schedules/${id}/run`, { method: "POST" }),
  previewCron: (cron: string, count = 5) => call<{ next: number[] }>("/schedules/preview", { method: "POST", ...json({ cron, count }) }),

  // inspecteur
  dataInfo: (t: PortRef) => call<PortInfo>(`${dataPath(t)}/info`),
  dataRows: (t: PortRef, q: DataQuery, offset: number, limit: number) =>
    call<RowsPage>(`${dataPath(t)}/rows`, { method: "POST", ...json({ ...q, offset, limit }) }),
  dataStats: (t: PortRef, geometry = false) => call<PortStats>(`${dataPath(t)}/stats?geometry=${geometry}`),
  dataValues: (t: PortRef, column: string, q: DataQuery, limit = 50) =>
    call<{ value: unknown; count: number }[]>(`${dataPath(t)}/values`, { method: "POST", ...json({ column, query: q, limit }) }),
  dataLocate: (t: PortRef, q: DataQuery, fid: number) =>
    call<{ index: number | null }>(`${dataPath(t)}/locate`, { method: "POST", ...json({ query: q, fid }) }),
  holdCache: (page: string, runs: string[]) => call<{ policy: string }>("/cache/hold", { method: "POST", ...json({ page, runs }) }),
  dataFeature: (t: PortRef, fid: number) => call<FeatureInfo>(`${dataPath(t)}/feature/${fid}`),
  rasterInfo: (t: PortRef, fid: number) => call<RasterInfo>(`${dataPath(t)}/raster/${fid}`),
  rasterPixel: (t: PortRef, fid: number, lon: number, lat: number) =>
    call<RasterPixel>(`${dataPath(t)}/raster/${fid}/pixel?lon=${lon}&lat=${lat}`),

  // assistant IA
  assistantConfig: () => call<AssistantConfig>("/assistant/config"),
  assistantSaveProvider: (id: string, body: Record<string, unknown>) =>
    call<AssistantProvider>(`/assistant/providers/${encodeURIComponent(id)}`, { method: "PUT", ...json(body) }),
  assistantDeleteProvider: (id: string) => call<void>(`/assistant/providers/${encodeURIComponent(id)}`, { method: "DELETE" }),
  assistantDefault: (id: string) => call<{ default: string }>(`/assistant/default/${encodeURIComponent(id)}`, { method: "PUT" }),
  assistantModels: (id: string) => call<{ models: string[] }>(`/assistant/providers/${encodeURIComponent(id)}/models`),
  assistantChats: () => call<ChatCard[]>("/assistant/chats"),
  assistantCreateChat: (provider: string, model: string) =>
    call<ChatRecord>("/assistant/chats", { method: "POST", ...json({ provider, model }) }),
  assistantChat: (id: string) => call<ChatRecord>(`/assistant/chats/${id}`),
  assistantDeleteChat: (id: string) => call<void>(`/assistant/chats/${id}`, { method: "DELETE" }),
  assistantInstallModifier: (body: { name: string; code: string; test_code: string }) =>
    call<{ installed: boolean; output: string; path?: string }>("/assistant/modifiers", { method: "POST", ...json(body) }),
};

const dataPath = (t: PortRef) => `/data/${t.run}/${t.node}/${t.port}`;

/** Exporte la vue filtrée : le fichier est généré par le serveur, on le télécharge. */
export async function exportData(t: PortRef, q: DataQuery, format: "csv" | "gpkg" | "parquet"): Promise<void> {
  const headers: Record<string, string> = { "Content-Type": "application/json", "Accept-Language": language() };
  const user = currentUser();
  if (user) headers["X-ETL-User"] = user;
  const res = await fetch(`/api${dataPath(t)}/export?format=${format}`, { method: "POST", headers, body: JSON.stringify({ ...q, offset: 0, limit: 1 }) });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, (body as { detail?: unknown }).detail);
  }
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = `${t.node}_${t.port}.${format}`;
  a.click();
  URL.revokeObjectURL(url);
}

/** Filtres de la table (sans tri ni pagination) en base64 URL : paramètre `q` des tuiles et du comptage de la vue. */
function encodeFilter(q: DataQuery): string {
  const filter = { filters: q.filters, where: q.where, search: q.search };
  const bytes = new TextEncoder().encode(JSON.stringify(filter));
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** Modèle d'URL des tuiles vectorielles d'un port : filtre et couleur font partie de l'URL, donc du cache. Elle ne
 * dépend pas de la vue : une tuile déjà reçue sert à nouveau. */
export function tileTemplate(t: PortRef, q: DataQuery, color: string | null): string {
  const params = `q=${encodeFilter(q)}${color ? `&color=${encodeURIComponent(color)}` : ""}`;
  return `${location.origin}/tiles/${t.run}/${t.node}/${t.port}/{z}/{x}/{y}.pbf?${params}`;
}

/** Modèle d'URL des tuiles PNG d'un raster (ligne `fid` du port) ; le style fait partie de l'URL, donc du cache. */
export function rasterTileTemplate(t: PortRef, fid: number, style: RasterStyle): string {
  const params = new URLSearchParams({ bands: style.bands.join(","), palette: style.palette });
  if (style.range) {
    params.set("vmin", String(style.range[0]));
    params.set("vmax", String(style.range[1]));
  }
  return `${location.origin}/tiles/raster/${t.run}/${t.node}/${t.port}/${fid}/{z}/{x}/{y}.png?${params}`;
}

/** Envoie un message à l'assistant et lit la réponse en flux (SSE par POST : EventSource ne sait pas poster). */
export async function streamAssistant(chatId: string, body: SendBody, onEvent: (e: AssistantEvent) => void, signal: AbortSignal): Promise<void> {
  const headers: Record<string, string> = { "Content-Type": "application/json", "Accept-Language": language() };
  const user = currentUser();
  if (user) headers["X-ETL-User"] = user;
  const res = await fetch(`/api/assistant/chats/${chatId}/messages`, { method: "POST", headers, body: JSON.stringify(body), signal });
  if (!res.ok || !res.body) {
    const text = await res.text();
    let detail: unknown = text;
    try {
      detail = JSON.parse(text).detail;
    } catch {
      /* corps non JSON */
    }
    throw new ApiError(res.status, detail);
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let end: number;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const data = block.split("\n").find((l) => l.startsWith("data: "));
      if (data) onEvent(JSON.parse(data.slice(6)) as AssistantEvent);
    }
  }
}

const EVENT_TYPES = ["run_started", "node", "counts", "log", "run_finished"];

/** Abonnement SSE : EventSource se reconnecte seul et renvoie `Last-Event-ID`, donc pas de rejeu. */
export function subscribeRun(
  runId: string,
  after: number,
  onEvent: (e: RunEvent) => void,
  onEnd: (status: string) => void,
): () => void {
  const source = new EventSource(`/api/runs/${runId}/events?after=${after}`);
  for (const type of EVENT_TYPES) {
    source.addEventListener(type, (msg) => onEvent(JSON.parse((msg as MessageEvent).data) as RunEvent));
  }
  source.addEventListener("end", (msg) => {
    source.close();
    onEnd((JSON.parse((msg as MessageEvent).data) as { status: string }).status);
  });
  return () => source.close();
}
