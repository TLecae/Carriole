import { _electron as electron, expect, test, type ElectronApplication } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { mainWindow } from "./windows";

const desktop = path.resolve(__dirname, "..");
const repo = path.resolve(desktop, "..");
const python = path.join(repo, ".venv", "Scripts", "python.exe");

async function launch(home: string, extraArgs: string[] = []): Promise<ElectronApplication> {
  const env = { ...process.env, ETL_HOME: home, ETL_PYTHON: python, PYTHONUTF8: "1" } as Record<string, string>;
  delete env.ELECTRON_RUN_AS_NODE; // posée par VS Code : Electron se lancerait comme un simple Node
  // données de Chromium dans le dossier d'essai : une application déjà ouverte sur ce poste ne gêne pas (instance unique)
  return electron.launch({ args: [`--user-data-dir=${path.join(home, "userdata")}`, desktop, ...extraArgs], env });
}

const serverJson = (home: string) => path.join(home, "data", "server.json");

async function backendUp(home: string): Promise<boolean> {
  try {
    const info = JSON.parse(fs.readFileSync(serverJson(home), "utf-8"));
    const res = await fetch(`http://127.0.0.1:${info.port}/api/health`, { signal: AbortSignal.timeout(1500) });
    return res.ok;
  } catch {
    return false;
  }
}

async function waitDown(home: string) {
  const deadline = Date.now() + 90_000;
  while (Date.now() < deadline && (await backendUp(home))) await new Promise((r) => setTimeout(r, 500));
}

test("fenêtre, dialogues natifs, ouverture depuis le menu, arrêt du moteur à la sortie", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "etl-desktop-"));
  const folder = path.join(home, "flux");
  fs.mkdirSync(folder);
  const app = await launch(home);
  try {
    // pendant que le moteur démarre : une petite fenêtre dit que l'ouverture est en cours
    await expect.poll(() => app.windows().some((w) => w.url().startsWith("data:text/html")), { timeout: 15_000 }).toBe(true);
    const page = await mainWindow(app);
    // le moteur local est démarré et l'interface s'affiche (jeton posé par la connexion)
    await expect(page.getByRole("heading", { name: "Flux récents" })).toBeVisible();
    await expect.poll(() => app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().length)).toBe(1); // fenêtre d'ouverture fermée
    expect(page.url()).toMatch(/^http:\/\/127\.0\.0\.1:\d+\//);
    expect(await app.evaluate(({ app: a }) => a.isPackaged)).toBe(false);

    // la page dispose des dialogues natifs
    expect(await page.evaluate(() => typeof window.etlDesktop?.pickFile)).toBe("function");

    // enregistrer : « Enregistrer sous » ouvre le dialogue natif (simulé côté processus principal)
    const target = path.join(folder, "natif.json");
    await app.evaluate(({ dialog }, file) => {
      dialog.showSaveDialog = (async () => ({ canceled: false, filePath: file })) as typeof dialog.showSaveDialog;
    }, target);
    await page.getByRole("button", { name: "+ Nouveau flux" }).click();
    const reader = page.getByTestId("palette-CsvReader");
    await reader.evaluate((el) => el.closest("details")?.setAttribute("open", "")); // catégories repliées à l'ouverture
    await reader.dblclick();
    await page.getByTestId("node-CsvReader_1").click();
    await page.keyboard.press("Control+s");
    await expect(page.getByText("Flux enregistré")).toBeVisible();
    expect(JSON.parse(fs.readFileSync(target, "utf-8")).name).toBe("natif"); // le nom du fichier

    // « Parcourir… » : le dialogue d'ouverture natif renvoie le chemin choisi
    const data = path.join(home, "communes.csv");
    fs.writeFileSync(data, "id;nom\n1;Toulouse\n", "utf-8");
    await app.evaluate(({ dialog }, file) => {
      dialog.showOpenDialog = (async () => ({ canceled: false, filePaths: [file] })) as typeof dialog.showOpenDialog;
    }, data);
    await page.getByRole("button", { name: "Fichier : parcourir" }).click();
    await expect(page.getByLabel("Fichier", { exact: true })).toHaveValue(data);

    // menu « Nouveau flux » : la page repart d'un flux vide (confirmation si modifications : on accepte)
    page.on("dialog", (d) => void d.accept());
    await page.getByRole("button", { name: /Accueil/ }).click();
    await app.evaluate(({ BrowserWindow }, file) => {
      BrowserWindow.getAllWindows()[0].webContents.send("etl:open-file", file);
    }, target);
    await expect(page.getByTestId("flow-name")).toHaveText("natif");
  } finally {
    await app.close();
  }
  // quitter sans run en cours arrête le moteur
  await waitDown(home);
  expect(await backendUp(home)).toBe(false);
});

test("un second lancement réutilise le moteur et ouvre le flux demandé", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "etl-desktop-"));
  const flow = path.join(home, "demande.json");
  fs.writeFileSync(flow, JSON.stringify({ name: "Flux demandé", nodes: [], links: [] }), "utf-8");
  const app = await launch(home, [flow]); // le chemin passé en argument s'ouvre au démarrage
  try {
    const page = await mainWindow(app);
    await expect(page.getByTestId("flow-name")).toHaveText("demande");
  } finally {
    await app.close();
  }
  await waitDown(home);
});

test("la langue choisie (réglage) s'applique à la page et au menu, et se change dans les paramètres", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "etl-desktop-"));
  fs.mkdirSync(path.join(home, "config"), { recursive: true });
  fs.writeFileSync(path.join(home, "config", "settings.json"), JSON.stringify({ language: "en" }), "utf-8");
  const app = await launch(home);
  try {
    const page = await mainWindow(app);
    await expect(page.getByRole("heading", { name: "Recent flows" })).toBeVisible();
    const labels = () => app.evaluate(({ Menu }) => Menu.getApplicationMenu()?.items.map((i) => i.label) ?? []);
    expect(await labels()).toEqual(["File", "View", "Help"]);

    // retour au français depuis les paramètres : la page, puis le menu du processus principal suivent
    await page.getByRole("button", { name: "Settings" }).click();
    await page.getByLabel("Interface language").selectOption("fr");
    await expect(page.getByRole("heading", { name: "Flux récents" })).toBeVisible();
    await expect.poll(labels).toEqual(["Fichier", "Affichage", "Aide"]);
    expect(JSON.parse(fs.readFileSync(path.join(home, "config", "settings.json"), "utf-8")).language).toBe("fr");
  } finally {
    await app.close();
  }
  await waitDown(home);
});

const closeWindow = (app: ElectronApplication) =>
  app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().find((w) => w.webContents.getURL().startsWith("http"))?.close());

test("fermer la fenêtre arrête complètement Carriole (par défaut)", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "etl-desktop-"));
  const app = await launch(home);
  const exited = new Promise<void>((resolve) => app.process().once("exit", () => resolve()));
  const page = await mainWindow(app);
  await expect(page.getByRole("heading", { name: "Flux récents" })).toBeVisible();
  await closeWindow(app);
  await exited; // l'application s'est arrêtée…
  await waitDown(home);
  expect(await backendUp(home)).toBe(false); // … et son moteur avec elle
});

test("réglage « arrière-plan » : fermer la fenêtre laisse Carriole et son moteur tourner", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "etl-desktop-"));
  const app = await launch(home);
  try {
    const page = await mainWindow(app);
    await page.getByRole("button", { name: "Paramètres" }).click();
    const keep = page.getByLabel(/Laisser Carriole tourner en arrière-plan/);
    await expect(keep).not.toBeChecked(); // par défaut : arrêt complet
    await keep.check();
    await expect.poll(() => JSON.parse(fs.readFileSync(path.join(home, "config", "settings.json"), "utf-8")).keep_running).toBe(true);
    await page.keyboard.press("Escape");
    await closeWindow(app);
    await expect.poll(() => app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().filter((w) => w.isVisible()).length)).toBe(0);
    expect(await app.evaluate(({ app: a }) => !!a)).toBe(true); // toujours là (zone de notification)
    expect(await backendUp(home)).toBe(true);
  } finally {
    await app.close();
  }
  await waitDown(home);
});
