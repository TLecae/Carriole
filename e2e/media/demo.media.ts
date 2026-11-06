import { expect, test, type Locator, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

// Vidéo et captures du site et du README (scripts/site_media.ps1). Données : les communes de la Haute-Garonne (API Géo
// d'Etalab), lues dans D:/donnees/communes_31.parquet, que le script prépare.
const LANG = process.env.MEDIA_LANG === "fr" ? "fr" : "en";
const OUT = process.env.MEDIA_OUT ?? path.resolve("media-out");
const DATA = "D:/donnees/communes_31.parquet";

const T = {
  fr: {
    flow: "Densité de population",
    newFlow: "+ Nouveau flux",
    flowName: "Nom du flux",
    search: "Rechercher un modificateur",
    file: "Fichier",
    target: "Système cible",
    attrName: "Nom de l'attribut",
    expression: "Expression",
    data: "Données",
    colorBy: "Colorer par",
    fullPage: "⤢ Pleine page",
    density: "densite",
    readQuery: "Requête de lecture",
    count: "Compter les entités",
    close: "Fermer",
    sort: "Trier par densite",
    port: "Port à inspecter",
    resizeBottom: "Redimensionner le panneau (glisser, ou flèches haut et bas)",
    exitFull: "✕ Fermer le plein écran",
    read: (name: string) => `Lire ${name}`,
    values: (name: string) => `Valeurs de ${name}`,
    attrs: "Attributs à créer",
    add: "+ Ajouter une ligne",
  },
  en: {
    flow: "Population density",
    newFlow: "+ New flow",
    flowName: "Flow name",
    search: "Search for a modifier",
    file: "File",
    target: "Target system",
    attrName: "Attribute name",
    expression: "Expression",
    data: "Data",
    colorBy: "Color by",
    fullPage: "⤢ Full page",
    density: "density",
    readQuery: "Read query",
    count: "Count the features",
    close: "Close",
    sort: "Sort by density",
    port: "Port to inspect",
    resizeBottom: "Resize the panel (drag, or up and down arrows)",
    exitFull: "✕ Close full screen",
    read: (name: string) => `Read ${name}`,
    values: (name: string) => `Values of ${name}`,
    attrs: "Attributes to create",
    add: "+ Add a row",
  },
}[LANG];

const DENSITY = `round("population" / (ST_Area(geometry) / 1e6), 1)`;

// Pointeur dessiné dans la page : la vidéo de Playwright n'en montre pas.
const CURSOR = `(() => {
  const install = () => {
    if (document.getElementById("demo-cursor")) return;
    const c = document.createElement("div");
    c.id = "demo-cursor";
    c.innerHTML = '<svg width="24" height="24" viewBox="0 0 24 24"><path d="M4 2 L4 19 L8.6 14.8 L11.7 21.6 L14.6 20.3 L11.6 13.6 L18 13.6 Z" fill="#fff" stroke="#111" stroke-width="1.5" stroke-linejoin="round"/></svg>';
    Object.assign(c.style, { position: "fixed", left: "0", top: "0", zIndex: "2147483647", pointerEvents: "none", transform: "translate(-100px, -100px)" });
    document.documentElement.appendChild(c);
    const move = (e) => { c.style.transform = "translate(" + (e.clientX - 4) + "px, " + (e.clientY - 2) + "px)"; };
    for (const t of ["mousemove", "pointermove", "dragover", "pointerdown"]) window.addEventListener(t, move, true);
    window.addEventListener("pointerdown", (e) => {
      const r = document.createElement("div");
      Object.assign(r.style, { position: "fixed", left: e.clientX - 14 + "px", top: e.clientY - 14 + "px", width: "28px", height: "28px", borderRadius: "50%", background: "rgba(31, 78, 140, .35)", zIndex: "2147483646", pointerEvents: "none", transition: "transform .4s ease-out, opacity .4s ease-out" });
      document.documentElement.appendChild(r);
      requestAnimationFrame(() => { r.style.transform = "scale(1.8)"; r.style.opacity = "0"; });
      setTimeout(() => r.remove(), 450);
    }, true);
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", install); else install();
})();`;

const pause = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** La souris d'une démonstration : elle se déplace, clique et tape à vitesse humaine. */
class Hand {
  constructor(private page: Page) {}

  async to(target: Locator | { x: number; y: number }, steps = 22): Promise<{ x: number; y: number }> {
    const p = "x" in target ? target : await center(target);
    await this.page.mouse.move(p.x, p.y, { steps });
    return p;
  }

  async click(target: Locator | { x: number; y: number }, wait = 350): Promise<void> {
    await this.to(target);
    await pause(120);
    await this.page.mouse.down();
    await this.page.mouse.up();
    await pause(wait);
  }

  async dblclick(target: Locator): Promise<void> {
    const p = await this.to(target);
    await pause(120);
    await this.page.mouse.dblclick(p.x, p.y);
    await pause(400);
  }

  async drag(from: Locator | { x: number; y: number }, to: Locator | { x: number; y: number }): Promise<void> {
    await this.to(from);
    await pause(120);
    await this.page.mouse.down();
    await this.to(to, 30);
    await pause(100);
    await this.page.mouse.up();
    await pause(400);
  }

  async type(field: Locator, text: string, replace = false): Promise<void> {
    await this.click(field, 150);
    if (replace) await field.press("Control+a");
    await field.pressSequentially(text, { delay: 38 });
    await pause(300);
  }
}

async function center(target: Locator): Promise<{ x: number; y: number }> {
  await target.scrollIntoViewIfNeeded();
  const box = (await target.boundingBox())!;
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

const handle = (page: Page, node: string, port: string, side: "source" | "target") =>
  page.locator(`.react-flow__handle.${side}[data-nodeid="${node}"][data-handleid="${port}"]`);

async function start(page: Page): Promise<void> {
  // jeton du serveur de test (playwright.config.ts), comme l'application le pose : en cookie
  await page.context().addCookies([{ name: "etl_token", value: "e2e-token", url: `http://127.0.0.1:${process.env.E2E_PORT ?? 18080}` }]);
  await page.request.put("/api/preferences/language", { data: { language: LANG } });
  await page.addInitScript(CURSOR);
}

/** Un modificateur de la palette, cherché par son nom, glissé sur le canevas à `at`. */
async function add(page: Page, hand: Hand, search: string, type: string, at: { x: number; y: number }): Promise<Locator> {
  await hand.type(page.getByLabel(T.search), search, true);
  await hand.drag(page.getByTestId(`palette-${type}`), at);
  const node = page.getByTestId(`node-${type}_1`);
  await expect(node).toBeVisible();
  return node;
}

test("vidéo : un flux construit, lancé, puis inspecté", async ({ page }) => {
  const t0 = Date.now(); // la vidéo commence avec la page
  await start(page);
  const hand = new Hand(page);
  await page.goto("/");
  await pause(1500);
  await hand.click(page.getByRole("button", { name: T.newFlow }), 900);
  await hand.type(page.getByLabel(T.flowName), T.flow, true);

  // trois modificateurs, de gauche à droite ; le premier recentre le canevas : on le remet à sa place
  const reader = await add(page, hand, "parquet", "GeoParquetReader", { x: 345, y: 190 });
  await hand.click(page.getByRole("button", { name: "zoom out" }), 500);
  await hand.drag(reader.locator(".node-head"), { x: 330, y: 180 });
  await hand.type(page.getByLabel(T.file, { exact: true }), DATA);
  await pause(600);
  await add(page, hand, "reproj", "Reproject", { x: 540, y: 180 });
  await hand.type(page.getByLabel(T.target, { exact: true }), "EPSG:2154");
  await add(page, hand, LANG === "fr" ? "créer des attr" : "create attr", "AddAttributes", { x: 750, y: 180 });
  await hand.click(page.getByRole("group", { name: T.attrs }).getByRole("button", { name: T.add, exact: true }));
  await hand.type(page.getByLabel(T.attrName, { exact: true }), T.density);
  await hand.type(page.getByRole("textbox", { name: T.expression, exact: true }), DENSITY);
  await page.getByLabel(T.search).fill("");

  // branchements, sortie vers entrée
  await hand.drag(handle(page, "GeoParquetReader_1", "output", "source"), handle(page, "Reproject_1", "input", "target"));
  await hand.drag(handle(page, "Reproject_1", "output", "source"), handle(page, "AddAttributes_1", "input", "target"));
  await expect(page.locator(".react-flow__edge")).toHaveCount(2);
  await pause(500);

  // lancement, puis le résultat dans l'inspecteur : table et carte, colorée par densité
  await hand.click(page.locator("button.run"));
  await expect(page.getByTestId("run-status")).toHaveText("done", { timeout: 60_000 });
  await pause(800);
  // image d'attente de la vidéo : le flux construit et lancé (scripts/site_media.ps1)
  fs.writeFileSync(path.join(OUT, `poster-${LANG}.txt`), ((Date.now() - t0) / 1000 - 0.3).toFixed(1));
  await hand.click(page.getByRole("tab", { name: T.data }));
  await hand.click(page.getByTestId("node-AddAttributes_1").locator(".node-head"), 800);
  await expect(page.getByTestId("data-row").first()).toBeVisible();
  await hand.click(page.getByRole("button", { name: T.fullPage }), 1500);
  await expect(page.getByTestId("map")).toBeVisible();
  const color = page.getByLabel(T.colorBy);
  await hand.to(color);
  await color.selectOption(T.density);
  await pause(2500);
  // table triée par densité décroissante ; une ligne choisie : la carte la montre
  const sort = page.getByRole("button", { name: T.sort });
  await hand.click(sort, 900);
  await hand.click(sort, 1200);
  await hand.click(page.getByTestId("data-row").first(), 2500);
  // un clic sur la carte : la commune et sa ligne, sans déplacer la carte
  const map = (await page.getByTestId("map").boundingBox())!;
  await hand.click({ x: map.x + map.width * 0.47, y: map.y + map.height * 0.32 }, 2000);
  await hand.to({ x: map.x + map.width * 0.6, y: map.y + map.height * 0.45 }, 40);
  await pause(2000);
});

test("captures : éditeur, inspecteur, requête de lecture", async ({ page }) => {
  await start(page);
  const node = (id: string, type: string, x: number, params: Record<string, unknown>) => ({ id, type, position: [x, 0], params });
  const flow = {
    name: T.flow,
    nodes: [
      node("GeoParquetReader_1", "GeoParquetReader", 0, { path: DATA }),
      node("Reproject_1", "Reproject", 320, { target: "EPSG:2154" }),
      node("AddAttributes_1", "AddAttributes", 600, { attributes: [{ name: T.density, expression: DENSITY }] }),
    ],
    links: [
      { id: "l1", from: ["GeoParquetReader_1", "output"], to: ["Reproject_1", "input"] },
      { id: "l2", from: ["Reproject_1", "output"], to: ["AddAttributes_1", "input"] },
    ],
  };
  const created = await (await page.request.post("/api/flows", { data: { flow } })).json();
  await page.goto(`/#flow=${created.id}`);
  await page.locator("button.run").click();
  await expect(page.getByTestId("run-status")).toHaveText("done", { timeout: 60_000 });

  // inspecteur en pleine page : le port choisi dans la liste, la carte calée sur ses données
  await page.getByRole("tab", { name: T.data }).click();
  await page.getByRole("button", { name: T.fullPage }).click();
  await page.getByLabel(T.port, { exact: true }).selectOption({ label: "AddAttributes_1 / output (586)" });
  await expect(page.getByTestId("data-row").first()).toBeVisible();
  await page.getByLabel(T.colorBy).selectOption(T.density);
  const sort = page.getByRole("button", { name: T.sort });
  await sort.click();
  await sort.click();
  await expect(page.getByTestId("data-row").first()).toContainText("Toulouse");
  await page.getByTestId("data-row").first().locator("div").nth(1).click();
  await pause(5000); // tuiles du fond de carte
  await page.mouse.move(1490, 890);
  await page.screenshot({ path: path.join(OUT, `inspecteur-${LANG}.png`) });
  await page.getByRole("button", { name: T.exitFull }).click();

  // éditeur : le flux, le formulaire du nœud, sa sortie sur la table et la carte (panneau du bas agrandi)
  const bar = (await page.getByRole("separator", { name: T.resizeBottom }).boundingBox())!;
  await page.mouse.move(bar.x + bar.width / 2, bar.y + bar.height / 2);
  await page.mouse.down();
  await page.mouse.move(bar.x + bar.width / 2, 400, { steps: 10 });
  await page.mouse.up();
  await page.getByTestId("node-AddAttributes_1").locator(".node-head").click();
  await page.getByTestId("minimap-toggle").click(); // carte de situation repliée : le canevas est bas
  await page.getByRole("button", { name: "fit view" }).click();
  await expect(page.getByTestId("data-row").first()).toBeVisible();
  await pause(5000);
  await page.mouse.move(1490, 890);
  await page.screenshot({ path: path.join(OUT, `editeur-${LANG}.png`) });

  // requête de lecture du lecteur : attributs cochés, filtre, valeurs d'une colonne, comptage
  await page.getByTestId("node-GeoParquetReader_1").locator(".node-head").click();
  await page.getByRole("button", { name: T.readQuery }).click();
  const overlay = page.getByRole("dialog", { name: T.readQuery });
  const helper = overlay.getByRole("complementary");
  await helper.getByRole("checkbox", { name: T.read("code") }).uncheck();
  await overlay.locator(".cm-content").click();
  await page.keyboard.type(`"population" > 10000`);
  await overlay.getByRole("button", { name: T.count }).click();
  await expect(overlay.locator(".sql-result")).toContainText(/\d/);
  await page.mouse.move(1490, 890);
  await pause(800);
  await page.screenshot({ path: path.join(OUT, `requete-${LANG}.png`) });
  fs.writeFileSync(path.join(OUT, `done-${LANG}.txt`), "ok");
});
