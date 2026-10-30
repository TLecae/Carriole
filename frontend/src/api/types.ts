// Types partagés avec l'API (voir api/ et engine/etl_engine/flow.py)

export interface JsonSchema {
  type?: string | string[];
  title?: string;
  description?: string;
  default?: unknown;
  enum?: unknown[];
  properties?: Record<string, JsonSchema>;
  items?: JsonSchema;
  $ref?: string;
  anyOf?: JsonSchema[];
  $defs?: Record<string, JsonSchema>;
  examples?: unknown[];
  "x-etl-widget"?: string;
  "x-etl-language"?: "python" | "sql"; // champ de code : langage de l'éditeur
  "x-etl-access"?: "read" | "write"; // champ chemin : dossiers parcourus par l'explorateur de fichiers
  "x-etl-input"?: string; // champ attribut d'un modificateur à plusieurs entrées : l'entrée dont il vient
  [key: string]: unknown;
}

export interface ModifierInfo {
  name: string;
  label: string;
  category: string;
  hidden?: boolean; // ancien modificateur : hors de la palette, encore exécuté dans les flux existants
  category_id?: string; // catégorie dans la langue source : clé stable (couleurs), quelle que soit la langue affichée
  description: string;
  help: string;
  version: number;
  mode: "streaming" | "blocking";
  inputs: string[];
  outputs: string[]; // contient « Rejected » quand le modificateur en a un
  params_schema: JsonSchema;
  component?: boolean; // composant : un flux réutilisable présenté comme un modificateur
  flow_id?: string; // flux qui définit le composant
  latest_version?: number | null; // composant : dernière version publiée
}

export interface PublishedParam {
  name: string;
  label: string;
  type: "text" | "number" | "bool" | "choice" | "path" | "connection";
  default: string;
  required: boolean;
  choices: string[];
}

export interface FlowNodeDoc {
  id: string;
  type: string;
  label: string;
  params: Record<string, unknown>;
  position: [number, number];
  enabled: boolean;
  component_version?: number | null; // nœud composant : version épinglée (null : la dernière)
}

export interface FlowLinkDoc {
  id: string;
  from: [string, string];
  to: [string, string];
  enabled: boolean;
}

export interface AnnotationDoc {
  id: string;
  text: string;
  position: [number, number];
}

export interface BookmarkDoc {
  id: string;
  name: string;
  color: string;
  nodes: string[];
  collapsed: boolean;
}

export interface ComponentMeta {
  name: string;
  label: string;
  category: string;
  description: string;
}

export interface FlowDoc {
  component?: ComponentMeta | null;
  schema_version: number;
  name: string;
  description: string;
  published_params: PublishedParam[];
  variables?: Record<string, string>;
  nodes: FlowNodeDoc[];
  links: FlowLinkDoc[];
  annotations: AnnotationDoc[];
  bookmarks: BookmarkDoc[];
  subflows: Record<string, unknown>;
}

export interface FlowSummary {
  id: string;
  name: string;
  component?: string | null; // nom du composant si ce flux en est un
  owner: string;
  updated_at: number;
  revision: number | string | null; // numéro (base) ou empreinte du fichier (flux en fichiers)
  path?: string; // flux en fichiers : emplacement du fichier
  description?: string; // absents de la réponse d'un enregistrement (voir FlowRecord)
  node_count?: number;
}

export interface FlowRecord extends FlowSummary {
  flow: FlowDoc;
}

export interface Issue {
  level: "error" | "warning";
  code: string;
  message: string;
  node: string | null;
}

export interface SchemaInfo {
  fields: { name: string; type: string }[];
  geometry: boolean;
  crs: string | null;
}

/** Préférences de l'utilisateur côté serveur : langue choisie et langues disponibles. */
export interface Preferences {
  language: string;
  configured: boolean; // faux : la langue n'a jamais été choisie (installation, première ouverture)
  languages: { code: string; name: string }[];
}

/** Réglages de l'application (fenêtre Paramètres) ; `null` : valeur par défaut. */
export interface AppSettingsValues {
  workers: number | null;
  memory_budget_mb: number | null;
  cache_dir: string; // vide : le dossier par défaut (`paths.cache`)
  proxy: string;
  component_dirs: string[];
  tile_hosts: string[];
  variables: Record<string, string>;
  keep_running: boolean | null; // poste de bureau : en arrière-plan à la fermeture (null : choix de l'installeur)
}

export interface AppSettings {
  version: string;
  values: AppSettingsValues;
  defaults: { workers: number };
  component_dirs_found: Record<string, boolean>;
  paths: { data: string; settings: string; logs: string; plugins: string; cache: string };
  restart_required?: string[];
}

export interface Analysis {
  /** Ports réels de chaque nœud : certains dépendent des paramètres (RouteByExpression, ExecuteSql…). */
  ports?: Record<string, { inputs: string[]; outputs: string[] }>;
  issues: Issue[];
  schemas: Record<string, SchemaInfo | null>; // clé : « nœud/port »
  order: string[];
  aliases?: Record<string, string>; // « composant/port » -> « nœud interne/port »
}

export type RunStatus = "queued" | "running" | "done" | "error" | "cancelled" | "interrupted";
export type NodeStatus = "pending" | "running" | "done" | "cached" | "error" | "cancelled" | "disabled" | "not_run";

export interface NodeResult {
  id: string;
  status: NodeStatus;
  seconds: number;
  rows: Record<string, number>;
  rows_in: Record<string, number>;
  rss_mb: number | null;
  error: string | null;
}

export interface RunSummary {
  id: string;
  flow_id: string | null;
  flow_name: string;
  status: RunStatus;
  owner: string;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  error: string | null;
  options: {
    cache?: boolean;
    target?: string | null;
    reuse_from?: string | null;
    notify_email?: string | null;
    batch?: { id: string; index: number; size: number; label: string }; // run d'un lot
  };
  params: Record<string, string>;
  purged_at: number | null; // données supprimées par la purge du cache
}

export interface RunDetail extends RunSummary {
  flow: FlowDoc;
  result: { status: string; seconds: number; error: string | null; nodes: Record<string, NodeResult> } | null;
  counts: Record<string, number>;
}

export interface RunEvent {
  id?: number;
  type: string;
  ts?: number;
  node?: string;
  port?: string;
  count?: number;
  status?: string;
  level?: string;
  message?: string;
  seconds?: number;
  rows?: Record<string, number>;
  rows_in?: Record<string, number>;
  rss_mb?: number | null;
  error?: string | null;
  nodes?: string[];
}

// ---- inspecteur -----------------------------------------------------------------------------

export interface PortRef {
  run: string;
  node: string;
  port: string;
}

export type FilterOp =
  | "contains"
  | "not_contains"
  | "starts"
  | "ends"
  | "equals"
  | "not_equals"
  | "gt"
  | "gte"
  | "lt"
  | "lte"
  | "between"
  | "in"
  | "null"
  | "not_null";

export interface ColumnFilter {
  column: string;
  op: FilterOp;
  value?: unknown;
  value2?: unknown;
  values?: unknown[];
}

export interface SortKey {
  column: string;
  desc: boolean;
}

/** Ce qui détermine l'ensemble d'entités affiché : partagé par la table, la carte et l'export. */
export interface DataQuery {
  filters: ColumnFilter[];
  where: string;
  search: string;
  sort: SortKey[];
}

export interface PortInfo {
  run: string;
  node: string;
  port: string;
  total: number; // entités du port
  columns: { name: string; type: string; category: "numeric" | "temporal" | "bool" | "text" | "other" }[];
  geometry: {
    crs: string | null;
    types: string[];
    extent_3857: number[] | null;
    map: boolean; // carte prête
    // carte : prête, en préparation (arrière-plan), en échec, impossible (système de coordonnées inconnu)
    map_status: "ready" | "building" | "failed" | "none";
    map_error: string | null; // « memory » : géométries trop volumineuses ; sinon l'erreur
  } | null;
}

export interface RowsPage {
  columns: string[]; // fid, attributs…, geometry (type de géométrie)
  total: number;
  filtered: number;
  rows: unknown[][];
}

export interface ColumnStats {
  type: string;
  min: unknown;
  max: unknown;
  distinct: number | null;
  nulls: number | null;
}

export interface PortStats {
  columns: Record<string, ColumnStats>;
  geometry?: { crs: string | null; types: string[]; extent_3857?: number[] | null; invalid?: number; empty?: number; null?: number };
}

export type GeoJsonGeometry = { type: string; coordinates?: unknown; geometries?: unknown };

export interface FeatureInfo {
  fid: number;
  attributes: Record<string, unknown>;
  bbox?: [number, number, number, number]; // WGS84 : ouest, sud, est, nord
  geometry?: GeoJsonGeometry; // WGS84 : sert à surligner l'entité choisie
  outline?: "edges"; // polygone découpé (énorme) : son contour est dans les lignes de `geometry`, pas le bord des pièces
}

/** Type de base d'une connexion. */
export type DbKind = "postgres" | "oracle";

export interface ConnectionInfo {
  name: string;
  kind: DbKind;
  host: string;
  port: number;
  dbname: string;
  user: string;
  sslmode: string;
  options: ConnectionOptions;
  has_password: boolean;
  owner: string;
  updated_at: number;
}

/** Réglages propres à un type de base (aucun type n'en a aujourd'hui). */
export type ConnectionOptions = Record<string, unknown>;

export interface ConnectionInput {
  kind: DbKind;
  host: string;
  port: number;
  dbname: string; // Oracle : le service
  user: string;
  sslmode: string;
  options: ConnectionOptions;
  password?: string | null; // absent : on garde le mot de passe actuel
}

/** Une table ou vue d'une connexion (liste lue sur le serveur). */
export interface PgTableInfo {
  schema: string;
  table: string;
  kind: string; // r : table, v : vue, m : vue matérialisée, p : table partitionnée
  rows_estimate: number;
  geometry: string | null; // première colonne géométrique (PostGIS)
}

export interface ConnectionTest {
  ok: boolean;
  server?: string;
  postgis?: string | null;
  spatial?: string | null; // extension ou types spatiaux trouvés (PostGIS 3.5, Oracle Spatial…)
  error?: string;
}

export interface ScheduleInfo {
  warnings?: string[]; // ex. chemin sur un lecteur réseau mappé
  id: string;
  name: string;
  flow_id: string;
  flow_name: string | null;
  cron: string;
  params: Record<string, string>;
  options: { cache?: boolean; notify_email?: string | null; allow_overlap?: boolean };
  enabled: boolean;
  owner: string;
  next_run_at: number | null;
  last_run_at: number | null;
  last_run_id: string | null;
  last_error: string | null;
}

export interface ScheduleInput {
  name: string;
  flow_id: string;
  cron: string;
  params: Record<string, string>;
  cache: boolean;
  notify_email: string | null;
  allow_overlap: boolean;
  enabled: boolean;
}

export interface ComponentVersion {
  name: string;
  version: number;
  note: string;
  author: string;
  created_at: number;
}

export interface ComponentVersions {
  versions: ComponentVersion[]; // la plus récente d'abord
  draft_changed: boolean; // le flux enregistré diffère de la dernière version
}

export interface FileEntry {
  name: string;
  path: string;
  kind: "dir" | "file";
  size: number | null;
  modified: number;
}

export interface FileRoot {
  path: string;
  label: string;
  available: boolean;
  reason?: string | null; // pourquoi l'emplacement est inutilisable (ex. partage non monté)
}

export interface FileListing {
  path: string; // vide : liste des dossiers autorisés
  parent: string | null; // null : on est à la racine d'un dossier autorisé
  entries: FileEntry[];
  truncated: boolean;
  roots: FileRoot[];
}

/** Un raster d'un port raster : description du fichier et réglages d'affichage par défaut. */
export interface RasterInfo {
  uri: string;
  driver: string;
  width: number;
  height: number;
  count: number;
  dtype: string;
  nodata: number | null;
  crs: string | null;
  bands: { index: number; name: string }[];
  display: { bands: number[]; ranges: Record<string, [number, number]> };
  palettes: string[];
}

export interface RasterStyle {
  bands: number[]; // une bande (palette) ou trois (rouge, vert, bleu)
  palette: string;
  range: [number, number] | null; // null : étirement automatique (percentiles 2 et 98)
}

export interface RasterPixel {
  inside: boolean;
  row?: number;
  col?: number;
  values: Record<string, number | null>;
}

// ---- assistant IA ----------------------------------------------------------------------------

export type PrivacyMode = "schema" | "sample";

export interface AssistantProvider {
  id: string;
  kind: "anthropic" | "openai" | "fake";
  label: string;
  base_url: string;
  model: string;
  privacy: PrivacyMode;
  sample_rows: number;
  has_key: boolean;
}

export interface AssistantPreset {
  id: string;
  kind: "anthropic" | "openai";
  label: string;
  base_url: string;
  model: string;
}

export interface AssistantConfig {
  providers: AssistantProvider[];
  default: string | null;
  presets: AssistantPreset[];
  modes: PrivacyMode[];
}

export interface ChatCard {
  id: string;
  title: string;
  provider: string;
  model: string;
  updated_at: number;
}

export type ChatBlock =
  | { type: "text"; text: string }
  | { type: "tool_use"; id: string; name: string; input: Record<string, unknown> }
  | { type: "tool_result"; tool_use_id: string; content: string; is_error?: boolean };

export interface Reference {
  kind: "file" | "port" | "node";
  path?: string;
  node?: string;
  port?: string;
  run?: string;
  id?: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string | ChatBlock[];
  ui?: { text: string; references: Reference[] };
}

export interface ChatRecord extends ChatCard {
  messages: ChatMessage[];
}

/** Proposition de l'assistant, à valider d'un clic. */
export type ActionData =
  | { operations: Record<string, unknown>[]; summary: string[]; flow: FlowDoc; issues: Issue[]; explanation: string }
  | { until_node: string | null }
  | { name: string; code: string; test_code: string; explanation: string }
  | { flow: FlowDoc };

export type AssistantEvent =
  | { type: "thinking"; step: number }
  | { type: "text"; delta: string }
  | { type: "tool_start"; id: string; name: string; input: Record<string, unknown>; summary: string }
  | { type: "tool"; id: string; name: string; input: Record<string, unknown>; ok: boolean; summary: string }
  | { type: "action"; id: string; name: string; data: ActionData }
  | { type: "error"; message: string }
  | { type: "done"; usage: Record<string, number> };

export interface SendBody {
  text: string;
  references: Reference[];
  flow: FlowDoc;
  run_id: string | null;
  notes: string[];
}
