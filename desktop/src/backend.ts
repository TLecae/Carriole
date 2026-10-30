import { execFile, spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

/** Ce que `etl serve` écrit dans server.json une fois l'API prête (voir etl_engine/backend.py). */
export interface ServerInfo {
  pid: number;
  port: number;
  token: string;
  started: number;
}

export const urlOf = (info: ServerInfo) => `http://127.0.0.1:${info.port}`;
export const loginUrlOf = (info: ServerInfo) => `${urlOf(info)}/?token=${info.token}`;

/**
 * Mêmes dossiers que etl_engine/settings.py : ETL_HOME, sinon %LOCALAPPDATA% et %APPDATA% (Windows),
 * $XDG_DATA_HOME et $XDG_CONFIG_HOME (Linux).
 */
export function appDirs(): { data: string; config: string } {
  const home = process.env.ETL_HOME?.trim();
  if (home) return { data: path.join(home, "data"), config: path.join(home, "config") };
  if (process.platform === "win32") {
    const local = process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local");
    const roaming = process.env.APPDATA || path.join(os.homedir(), "AppData", "Roaming");
    return { data: folder(local, "Carriole", "AtelierETL"), config: folder(roaming, "Carriole", "AtelierETL") };
  }
  const data = process.env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share");
  const config = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config");
  return { data: folder(data, "carriole", "atelier-etl"), config: folder(config, "carriole", "atelier-etl") };
}

/**
 * `parent/name` ; le dossier de l'ancien nom (`AtelierETL`) y est renommé s'il existe seul (même règle dans
 * etl_engine/settings.py). S'il ne peut pas l'être (fichiers ouverts par une ancienne version), il reste utilisé.
 */
function folder(parent: string, name: string, legacyName: string): string {
  const current = path.join(parent, name);
  const legacy = path.join(parent, legacyName);
  if (!fs.existsSync(current) && fs.existsSync(legacy)) {
    try {
      fs.renameSync(legacy, current);
    } catch {
      return legacy;
    }
  }
  return current;
}

export function readInfo(): ServerInfo | null {
  try {
    const raw = JSON.parse(fs.readFileSync(path.join(appDirs().data, "server.json"), "utf-8"));
    return { pid: Number(raw.pid), port: Number(raw.port), token: String(raw.token), started: Number(raw.started) };
  } catch {
    return null;
  }
}

export async function isHealthy(info: ServerInfo, timeoutMs = 1500): Promise<boolean> {
  try {
    const res = await fetch(`${urlOf(info)}/api/health`, { signal: AbortSignal.timeout(timeoutMs) });
    return res.ok;
  } catch {
    return false;
  }
}

export async function runningServer(): Promise<ServerInfo | null> {
  const info = readInfo();
  return info && (await isHealthy(info)) ? info : null;
}

export class BackendError extends Error {
  constructor(
    message: string,
    public status?: number,
  ) {
    super(message);
  }
}

/** Appel JSON de l'API locale avec le jeton. */
export async function call<T = unknown>(info: ServerInfo, method: string, apiPath: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "X-ETL-Token": info.token };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let res: Response;
  try {
    res = await fetch(`${urlOf(info)}${apiPath}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(30_000),
    });
  } catch (err) {
    throw new BackendError(`le backend ne répond pas : ${(err as Error).message}`);
  }
  const text = await res.text();
  const data = text ? JSON.parse(text) : undefined;
  if (!res.ok) {
    const detail = data?.detail;
    throw new BackendError(typeof detail === "string" ? detail : JSON.stringify(detail ?? data), res.status);
  }
  return data as T;
}

/** Le processus `pid` existe-t-il encore ? */
export function processAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

/** Sortie console du moteur (erreurs au démarrage, plantages), sinon ignorée. */
function consoleLog(file: string): number | "ignore" {
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    return fs.openSync(file, "a");
  } catch {
    return "ignore";
  }
}

/**
 * Erreur écrite dans `file` après l'octet `from` : la dernière exception Python (« ImportError: … »), plutôt que le
 * « Application startup failed. Exiting. » d'uvicorn qui la suit, sinon la dernière ligne.
 */
function lastLine(file: string, from: number): string {
  try {
    const fd = fs.openSync(file, "r");
    try {
      const size = fs.fstatSync(fd).size;
      const buf = Buffer.alloc(Math.max(0, Math.min(size - from, 64 * 1024)));
      fs.readSync(fd, buf, 0, buf.length, size - buf.length);
      const lines = buf.toString("utf8").split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
      const error = lines.findLast((l) => /^(?:\w+\.)*[A-Z]\w*(?:Error|Exception)\b/.test(l));
      return (error ?? lines.pop() ?? "").slice(0, 300);
    } finally {
      fs.closeSync(fd);
    }
  } catch {
    return "";
  }
}

/**
 * Démarre `etl serve` détaché (il survit à la fermeture de la fenêtre : les runs continuent) puis attend qu'il réponde.
 * Une instance déjà en marche (lancée par la ligne de commande, par exemple) est réutilisée. `same` : relance après un
 * arrêt, à la même adresse et avec le même jeton (la page ouverte continue sans se recharger).
 */
export async function ensureBackend(
  python: string,
  timeoutMs = 60_000,
  same?: { port: number; token: string },
): Promise<ServerInfo> {
  const existing = await runningServer();
  if (existing) return existing;
  const log = path.join(appDirs().data, "logs", "server-console.log");
  const start = fs.statSync(log, { throwIfNoEntry: false })?.size ?? 0;
  const output = consoleLog(log);
  const child = spawn(python, ["-m", "etl_engine.cli", "serve", ...(same ? ["--port", String(same.port)] : [])], {
    detached: true,
    stdio: ["ignore", output, output],
    windowsHide: true,
    env: same ? { ...process.env, ETL_SERVE_TOKEN: same.token } : process.env,
  });
  if (typeof output === "number") fs.closeSync(output); // le moteur a sa propre copie
  let exited = false;
  child.on("exit", () => (exited = true));
  child.unref();
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const info = await runningServer();
    if (info) return info;
    if (exited) break; // port déjà pris, installation abîmée… : inutile d'attendre
    await new Promise((r) => setTimeout(r, 250));
  }
  const last = lastLine(log, start);
  const why = exited ? "le moteur s'est arrêté au démarrage" : "le moteur ne répond pas";
  throw new BackendError(`${why}${last ? ` : ${last}` : ""}\n\nJournal : ${log}`);
}

export async function stopBackend(info: ServerInfo, waitMs = 90_000): Promise<void> {
  try {
    await call(info, "POST", "/api/shutdown");
  } catch {
    return; // déjà arrêté
  }
  const deadline = Date.now() + waitMs;
  while (Date.now() < deadline && (await isHealthy(info, 500))) await new Promise((r) => setTimeout(r, 300));
}

export interface DesktopSettings {
  keep_running: boolean; // fermer la fenêtre laisse Carriole en arrière-plan (sinon : arrêt complet)
}

/** Valeur texte `name` d'une clé du registre (`HKLM\Software\Carriole`…), ou null. */
export function registryValue(hive: "HKLM" | "HKCU", key: string, name: string): Promise<string | null> {
  if (process.platform !== "win32") return Promise.resolve(null);
  return new Promise((resolve) => {
    execFile("reg", ["query", `${hive}\\Software\\${key}`, "/v", name], { windowsHide: true }, (error, stdout) => {
      const match = !error && new RegExp(`${name}\\s+REG_SZ\\s+(\\S+)`, "i").exec(stdout);
      resolve(match ? match[1] : null);
    });
  });
}

let installerKeepRunning = false;

/** Choix de l'installeur (« laisser tourner en arrière-plan ») : la valeur par défaut tant que l'utilisateur n'a rien
 * réglé dans les paramètres. À lire une fois au démarrage. */
export async function loadInstallerDefaults(): Promise<void> {
  installerKeepRunning = (await registryValue("HKLM", "Carriole", "KeepRunning")) === "1";
}

/** Réglages de l'application de bureau, lus dans settings.json ; sinon le choix de l'installeur ; sinon arrêt complet. */
export function desktopSettings(): DesktopSettings {
  try {
    const raw = JSON.parse(fs.readFileSync(path.join(appDirs().config, "settings.json"), "utf-8"));
    if (typeof raw?.keep_running === "boolean") return { keep_running: raw.keep_running };
  } catch {
    /* pas de réglage : valeur de l'installeur */
  }
  return { keep_running: installerKeepRunning };
}
