import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { IN, OUT } from "./paths";
import { addFromPalette, chooseMenu } from "./ui";

// Les scénarios écrivent dans un dossier temporaire (OUT) : les vérifications lisent le vrai fichier.
const out = (name: string) => path.join(process.env.E2E_OUT!, name);

async function quickAdd(page: Page, name: string) {
  // Taper sur le canvas ouvre l'ajout rapide ; la première lettre l'ouvre, le reste va dans le champ.
  await page.keyboard.press(name[0]);
  const input = page.getByPlaceholder("Nom du modificateur…");
  await expect(input).toBeVisible();
  await input.fill(name);
  await input.press("Enter");
}

test("construire, enregistrer, lancer et retrouver un flux", async ({ page }) => {
  fs.rmSync(out("e2e_out.csv"), { force: true });
  await page.goto("/#new");
  await page.getByLabel("Nom du flux").fill("Flux E2E");

  // 1. Lecture CSV : double-clic dans la palette, puis paramètres générés depuis le schéma
  await addFromPalette(page, "CsvReader");
  const reader = page.getByTestId("node-CsvReader_1");
  await expect(reader).toBeVisible();
  await page.getByLabel("Fichier", { exact: true }).fill(`${IN}/communes.csv`);
  await page.getByLabel("Séparateur", { exact: true }).fill(";");

  // 2. ExtractByExpression par ajout rapide : relié automatiquement au nœud sélectionné
  await reader.click(); // sort le focus du champ de saisie
  await quickAdd(page, "ExtractByExpression");
  await expect(page.getByTestId("node-ExtractByExpression_1")).toBeVisible();
  await page.getByRole("group", { name: "Conditions" }).getByRole("button", { name: "+ Ajouter" }).click();
  await page.getByLabel("Conditions 1").fill('CAST("pop" AS INTEGER) >= 1000');

  // 3. Écriture CSV sur la sortie « Passed »
  await page.getByTestId("node-ExtractByExpression_1").click();
  await quickAdd(page, "CsvWriter");
  await expect(page.getByTestId("node-CsvWriter_1")).toBeVisible();
  await page.getByLabel("Fichier", { exact: true }).fill(`${OUT}/e2e_out.csv`);

  // 4. Enregistrer (Ctrl+S) : le flux est repérable par l'URL
  await page.getByRole("button", { name: "fit view" }).click();
  await page.getByTestId("node-CsvWriter_1").click();
  await page.keyboard.press("Control+s");
  await expect(page.getByText("Flux enregistré")).toBeVisible();
  await expect(page).toHaveURL(/flow=[0-9a-f]+/);

  // 5. Lancer : le worker exécute, les comptes apparaissent sur les ports des nœuds
  await page.getByRole("button", { name: /Lancer/ }).first().click();
  await expect(page.getByTestId("run-status")).toHaveText("done");
  await expect(page.getByTestId("port-count-CsvReader_1-output")).toHaveText("20");
  await expect(page.getByTestId("port-count-ExtractByExpression_1-in-input")).toHaveText("20");
  await expect(page.getByTestId("port-count-ExtractByExpression_1-Passed")).toHaveText("10");
  await expect(page.getByTestId("port-count-ExtractByExpression_1-Failed")).toHaveText("10");
  // « Failed » n'est relié à rien : le nœud donne quand même son compte, et le détail au clic
  await page.getByTestId("node-ExtractByExpression_1").click();
  await expect(page.getByRole("region", { name: "Dernier run" })).toContainText("Failed : 10 entités");
  expect(fs.readFileSync(out("e2e_out.csv"), "utf-8").trim().split("\n")).toHaveLength(11); // en-tête + 10

  // 6. Le journal garde la trace du run (le clic sur le nœud a affiché l'onglet Données)
  await page.getByRole("tab", { name: "Journal" }).click();
  await expect(page.getByRole("log")).toContainText("entités lues");

  // Mise en cache (activée par défaut) : relancer sans rien changer reprend la lecture et le test du run précédent,
  // l'écriture est toujours refaite (le fichier de sortie est réécrit)
  await expect(page.getByTestId("cache-toggle")).toHaveAttribute("aria-pressed", "true");
  fs.rmSync(out("e2e_out.csv"));
  await page.getByRole("button", { name: /Lancer/ }).first().click();
  await expect(page.getByTestId("run-status")).toHaveText("done");
  await expect(page.getByTestId("node-CsvReader_1")).toContainText("cache");
  await expect(page.getByTestId("node-ExtractByExpression_1")).toContainText("cache");
  await expect(page.getByTestId("node-CsvWriter_1")).toContainText("terminé");
  expect(fs.readFileSync(out("e2e_out.csv"), "utf-8").trim().split("\n")).toHaveLength(11);

  // 7. Fermer la fenêtre et revenir : le run terminé est retrouvé avec ses comptes
  await page.reload();
  await expect(page.getByTestId("run-status")).toHaveText("done");
  await expect(page.getByTestId("port-count-CsvReader_1-output")).toHaveText("20");
  await expect(page.getByTestId("node-ExtractByExpression_1")).toBeVisible();
});

test("les avertissements signalent une erreur avant le lancement", async ({ page }) => {
  await page.goto("/#new");
  await addFromPalette(page, "ExtractByExpression");
  await expect(page.getByTestId("node-ExtractByExpression_1")).toBeVisible();
  await page.getByRole("tab", { name: /Avertissements/ }).click();
  await expect(page.getByRole("list")).toContainText("au moins une condition");
  await expect(page.getByRole("list")).toContainText("non connectée");
});

test("annuler et rétablir", async ({ page }) => {
  await page.goto("/#new");
  await addFromPalette(page, "CsvReader");
  await expect(page.getByTestId("node-CsvReader_1")).toBeVisible();
  await page.getByRole("button", { name: "Annuler" }).click();
  await expect(page.getByTestId("node-CsvReader_1")).toHaveCount(0);
  await page.getByRole("button", { name: "Rétablir" }).click();
  await expect(page.getByTestId("node-CsvReader_1")).toBeVisible();
});

test("sélection à la souris, puis Ctrl+clic retire un nœud de la sélection", async ({ page }) => {
  await page.goto("/#new");
  await addFromPalette(page, "CsvReader");
  await addFromPalette(page, "ExtractByExpression");
  const reader = page.getByTestId("node-CsvReader_1");
  const tester = page.getByTestId("node-ExtractByExpression_1");
  const a = (await reader.boundingBox())!;
  const b = (await tester.boundingBox())!;
  // cadre tiré sur le fond, autour des deux nœuds
  const left = Math.min(a.x, b.x) - 30;
  const top = Math.min(a.y, b.y) - 30;
  await page.mouse.move(left, top);
  await page.mouse.down();
  await page.mouse.move(Math.max(a.x + a.width, b.x + b.width) + 30, Math.max(a.y + a.height, b.y + b.height) + 30, { steps: 8 });
  await page.mouse.up();
  await expect(reader).toHaveClass(/selected/);
  await expect(tester).toHaveClass(/selected/);
  // le cadre de sélection ne doit pas intercepter le clic
  await tester.locator(".node-head").click({ modifiers: ["Control"] });
  await expect(tester).not.toHaveClass(/selected/);
  await expect(reader).toHaveClass(/selected/);
});

const TILE = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");

test("inspecter une connexion : table filtrée, tri, carte synchronisée", async ({ page }) => {
  const tiles: { url: string; status: number; type: string }[] = [];
  const logs: string[] = [];
  // Fonds de plan : pas de dépendance à internet (tuile factice), mais le navigateur doit laisser partir la requête
  // (CSP du backend : connect-src / img-src), sinon la carte reste vide.
  const basemapHosts = new Set<string>();
  await page.route(/\/\/(tile\.openstreetmap\.org|server\.arcgisonline\.com|data\.geopf\.fr)\//, (route) => {
    basemapHosts.add(new URL(route.request().url()).hostname);
    return route.fulfill({ status: 200, contentType: "image/png", body: TILE });
  });
  page.on("console", (m) => logs.push(`${m.type()}: ${m.text()}`));
  page.on("pageerror", (e) => logs.push(`pageerror: ${e.message}`));
  page.on("response", (r) => {
    if (r.url().includes("/tiles/")) tiles.push({ url: r.url(), status: r.status(), type: r.headers()["content-type"] ?? "" });
  });
  await page.goto("/#new");
  await page.getByLabel("Nom du flux").fill("Flux inspecteur");
  await addFromPalette(page, "CsvReader");
  await page.getByLabel("Fichier", { exact: true }).fill(`${IN}/lieux.csv`);
  await page.getByLabel("Séparateur", { exact: true }).fill(";");
  await page.getByLabel("Colonne WKT", { exact: true }).fill("wkt");
  await page.getByLabel("Système de coordonnées", { exact: true }).fill("EPSG:2154");
  await page.getByRole("button", { name: /Lancer/ }).first().click();
  await expect(page.getByTestId("run-status")).toHaveText("done");

  // Table : un nœud sélectionné montre sa sortie dans l'onglet Données
  await page.getByRole("tab", { name: "Données" }).click();
  await page.getByTestId("node-CsvReader_1").click();
  await expect(page.getByTestId("data-count")).toHaveText("20 entités");
  await expect(page.getByTestId("data-row").first()).toBeVisible();

  // Filtre de colonne : calculé côté serveur, le compte suit
  await page.getByLabel("Filtre nom", { exact: true }).fill("lieu 1");
  await page.getByLabel("Filtre nom", { exact: true }).press("Enter");
  await expect(page.getByTestId("data-count")).toHaveText("11 / 20 entités"); // lieu 1 et lieu 10 à 19

  // Tri : un clic croissant, un deuxième décroissant
  await page.getByRole("button", { name: "Trier par id" }).click();
  await page.getByRole("button", { name: "Trier par id" }).click();
  await expect(page.getByTestId("data-row").first()).toContainText("lieu 19");

  // Carte : les tuiles vectorielles filtrées arrivent du serveur
  await expect(page.getByTestId("map")).toBeVisible();
  await page.getByLabel("Colorer par").selectOption("pop");
  await expect(page.getByTestId("map")).toHaveAttribute("data-color", "pop");
  // La carte a rendu des entités à partir des tuiles reçues du serveur
  const rendered = async () => Number(await page.getByTestId("map").getAttribute("data-features"));
  try {
    await expect.poll(rendered, { message: "entités rendues" }).toBeGreaterThan(0);
  } catch (error) {
    const webgl = await page.evaluate(() => !!document.createElement("canvas").getContext("webgl2"));
    const attrs = await page.getByTestId("map").evaluate((el) => el.getAttributeNames().map((n) => `${n}=${el.getAttribute(n)}`));
    console.log("DIAGNOSTIC carte", { webgl, tuiles: tiles.length, premieres: tiles.slice(0, 3), attrs, console: logs.slice(-12) });
    throw error;
  }
  // toute tuile reçue est valide ; on nomme celles qui ne le sont pas pour comprendre un échec
  expect(tiles.filter((t) => !(t.status === 200 && t.type === "application/x-protobuf"))).toEqual([]);

  // Fond de plan : OpenStreetMap par défaut, puis satellite d'un clic
  await expect.poll(() => [...basemapHosts], { message: "tuiles OpenStreetMap" }).toContain("tile.openstreetmap.org");
  // les données (ici des points : pas de couche « fill ») restent AU-DESSUS du fond de plan, quel que soit le fond
  await expect(page.getByTestId("map")).toHaveAttribute("data-layer-order", /(^|,)basemap,data-0-/);
  await page.getByRole("button", { name: "Satellite" }).click();
  await expect.poll(() => [...basemapHosts], { message: "tuiles satellite" }).toContain("server.arcgisonline.com");
  await expect(page.getByTestId("map")).toHaveAttribute("data-layer-order", /(^|,)basemap,data-0-/);
  expect(logs.filter((l) => /Content Security Policy/i.test(l))).toEqual([]);
  await page.getByRole("button", { name: "Plan", exact: true }).click();

  // Table : élargir une colonne à la souris
  const head = page.locator(".table-head .cell.head").filter({ has: page.getByRole("button", { name: "Trier par nom" }) });
  const before = (await head.boundingBox())!.width;
  const grip = (await page.getByRole("separator", { name: "Largeur de la colonne nom" }).boundingBox())!;
  await page.mouse.move(grip.x + grip.width / 2, grip.y + 10);
  await page.mouse.down();
  await page.mouse.move(grip.x + 150, grip.y + 10, { steps: 5 });
  await page.mouse.up();
  await expect.poll(async () => (await head.boundingBox())!.width).toBeGreaterThan(before + 100);

  // Copier une valeur (clic droit ou Ctrl+C sur la cellule) et le nom d'une colonne (clic droit sur l'en-tête)
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  const clipboard = () => page.evaluate(() => navigator.clipboard.readText());
  const cell = page.getByTestId("data-row").first().getByText("lieu 19", { exact: true });
  await cell.click({ button: "right" });
  await page.getByRole("menuitem", { name: /Copier la valeur/ }).click();
  await expect.poll(clipboard).toBe("lieu 19");
  await head.click({ button: "right", position: { x: 20, y: 8 } });
  await page.getByRole("menuitem", { name: "Copier le nom de la colonne" }).click();
  await expect.poll(clipboard).toBe("nom");
  await page.getByTestId("data-row").nth(1).getByText("lieu 18", { exact: true }).click();
  await page.keyboard.press("Control+C");
  await expect.poll(clipboard).toBe("lieu 18");
  await page.getByTestId("data-row").nth(1).click(); // désélectionne l'entité

  // Sélection d'une ligne : surlignée dans la table
  await page.getByTestId("data-row").nth(2).click();
  await expect(page.getByTestId("data-row").nth(2)).toHaveClass(/selected/);

  // Clause WHERE libre : la table et la carte reçoivent le même filtre
  await page.getByLabel("Filtre nom", { exact: true }).fill("");
  await page.getByLabel("Filtre nom", { exact: true }).press("Enter");
  await page.getByLabel("Clause WHERE").fill('CAST("pop" AS INTEGER) >= 1500');
  await page.getByLabel("Clause WHERE").press("Enter");
  await expect(page.getByTestId("data-count")).toHaveText("5 / 20 entités");
  // la carte reçoit le même filtre : elle rend moins d'entités qu'avant (20 sans filtre)
  await expect.poll(rendered, { message: "carte filtrée" }).toBeLessThan(20);

  // Une clause invalide est refusée avec un message, sans casser l'écran
  await page.getByLabel("Clause WHERE").fill("1) OR (1=1");
  await page.getByLabel("Clause WHERE").press("Enter");
  await expect(page.getByRole("alert")).toContainText("déséquilibrés");
});

test("liste des runs : retrouver, ouvrir et relancer un run", async ({ page }) => {
  page.on("dialog", (d) => void d.accept()); // « modifications non enregistrées » : on remplace le flux affiché
  await page.goto("/#new");
  await page.getByLabel("Nom du flux").fill("Flux liste des runs");
  await addFromPalette(page, "CsvReader");
  await page.getByLabel("Fichier", { exact: true }).fill(`${IN}/communes.csv`);
  await page.getByLabel("Séparateur", { exact: true }).fill(";");
  await page.getByRole("button", { name: /Lancer/ }).first().click();
  await expect(page.getByTestId("run-status")).toHaveText("done");
  await expect(page.getByText("Run terminé")).toBeVisible(); // notification de fin dans l'interface

  await page.getByRole("button", { name: "Runs", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Runs" });
  await expect(dialog.getByTestId("run-row").first()).toHaveAttribute("data-status", "done");
  const before = await dialog.getByTestId("run-row").count();

  // Relancer : un nouveau run part du flux tel qu'il a été exécuté
  await dialog.getByRole("button", { name: "Relancer" }).first().click();
  await expect(page.getByTestId("run-status")).toHaveText("done");
  await expect(page.getByLabel("Nom du flux")).toHaveValue(/\(run [0-9a-f]{6}\)/);
  await page.getByRole("button", { name: "Runs", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Runs" }).getByTestId("run-row")).toHaveCount(before + 1);

  // Filtres de la liste
  await page.getByRole("tab", { name: "En échec" }).click();
  await expect(page.getByText("Aucun run.")).toBeVisible();
  await page.getByRole("tab", { name: "Terminés" }).click();
  await expect(page.getByTestId("run-row").first()).toHaveAttribute("data-status", "done");
});

// Demande un PostgreSQL de test (E2E_PG_HOST, E2E_PG_PASSWORD…) : sauté s'il n'y en a pas sur le poste.
test("connexions : créer, tester et choisir dans un modificateur", async ({ page }) => {
  test.skip(!process.env.E2E_PG_HOST, "E2E_PG_HOST non défini : pas de PostgreSQL de test");
  await page.goto("/#new");
  await chooseMenu(page, "Outils", "Connexions");
  const dialog = page.getByRole("dialog", { name: "Connexions" });
  await dialog.getByLabel("Nom de la connexion").fill("base e2e");
  await dialog.getByLabel("Hôte").fill(process.env.E2E_PG_HOST!);
  await dialog.getByLabel("Base", { exact: true }).fill(process.env.E2E_PG_DB ?? "etl_test");
  await dialog.getByLabel("Utilisateur de la base").fill(process.env.E2E_PG_USER ?? "postgres");
  await dialog.getByLabel("Mot de passe").fill(process.env.E2E_PG_PASSWORD ?? "test");
  await dialog.getByLabel("Mode SSL").selectOption("disable");
  await dialog.getByRole("button", { name: "Enregistrer" }).click();
  await expect(dialog.getByRole("status")).toContainText("enregistrée");
  await dialog.getByRole("button", { name: "Tester" }).click();
  await expect(dialog.getByRole("status")).toContainText("Connexion réussie");
  await expect(dialog.getByRole("status")).toContainText("PostGIS");
  // le mot de passe n'est jamais réaffiché
  await expect(dialog.getByLabel("Mot de passe")).toHaveValue("");
  await dialog.getByRole("button", { name: "Fermer" }).click();

  // La connexion est proposée dans le formulaire du modificateur, qui lit alors le schéma de la requête
  await addFromPalette(page, "PostgresReader");
  await expect(page.getByTestId("node-PostgresReader_1")).toBeVisible();
  await page.getByLabel("Connexion", { exact: true }).selectOption("base e2e");
  await page.getByLabel("Requête SQL").fill("SELECT 1 AS un, 'a' AS lettre");
  await page.getByRole("button", { name: /Lancer/ }).first().click();
  await expect(page.getByTestId("run-status")).toHaveText("done");
  await page.getByTestId("node-PostgresReader_1").click();
  await expect(page.getByRole("region", { name: "Dernier run" })).toContainText(/output : 1 entit/);
});

test("connexions : une base Oracle, proposée au seul lecteur Oracle", async ({ page }) => {
  await page.goto("/#new");
  await chooseMenu(page, "Outils", "Connexions");
  const dialog = page.getByRole("dialog", { name: "Connexions" });
  await dialog.getByLabel("Nom de la connexion").fill("entrepot e2e");
  await dialog.getByLabel("Type de base").selectOption("oracle");
  await expect(dialog.getByLabel("Port")).toHaveValue("1521"); // port par défaut du type
  await expect(dialog.getByLabel("Mode SSL")).toHaveCount(0); // réglage propre à PostgreSQL
  await expect(dialog.getByLabel("Type de base").locator("option")).toHaveText(["PostgreSQL / PostGIS", "Oracle"]);
  await dialog.getByLabel("Hôte").fill("ora.exemple");
  await dialog.getByLabel("Base", { exact: true }).fill("ORCLPDB1");
  await dialog.getByLabel("Utilisateur de la base").fill("carto");
  await dialog.getByRole("button", { name: "Enregistrer" }).click();
  await expect(dialog.getByRole("status")).toContainText("enregistrée");
  await expect(dialog.getByRole("listitem").filter({ hasText: "entrepot e2e" })).toContainText("Oracle");
  await dialog.getByRole("button", { name: "Fermer" }).click();

  await addFromPalette(page, "OracleReader");
  await expect(page.getByLabel("Connexion", { exact: true }).locator("option", { hasText: "entrepot e2e" })).toHaveCount(1);
  await addFromPalette(page, "PostgresReader");
  await expect(page.getByLabel("Connexion", { exact: true }).locator("option", { hasText: "entrepot e2e" })).toHaveCount(0);
  await page.request.delete("/api/connections/entrepot%20e2e");
});

test("composant : un flux réutilisable comme un modificateur", async ({ page }) => {
  const name = `filtre_e2e_${Date.now()}`;
  const component = {
    name: "Composant E2E",
    description: "Garde les communes d'au moins SEUIL habitants.",
    component: { name, label: "Filtre E2E", category: "Composants E2E" },
    published_params: [{ name: "SEUIL", label: "Seuil", default: "1000" }],
    nodes: [
      { id: "in", type: "ComponentInput", params: { port: "in" } },
      { id: "t", type: "ExtractByExpression", params: { conditions: ['CAST("pop" AS INTEGER) >= ${SEUIL}'] } },
      { id: "ok", type: "ComponentOutput", params: { port: "ok" } },
    ],
    links: [
      { id: "a", from: ["in", "output"], to: ["t", "input"] },
      { id: "b", from: ["t", "Passed"], to: ["ok", "input"] },
    ],
  };
  const created = await page.request.post("/api/flows", { data: { flow: component } });
  expect(created.ok()).toBeTruthy();
  fs.rmSync(out("e2e_composant.csv"), { force: true });

  page.on("dialog", (d) => void d.accept());
  await page.goto("/#new");
  await page.getByLabel("Nom du flux").fill("Flux avec composant");
  await addFromPalette(page, "CsvReader");
  await page.getByLabel("Fichier", { exact: true }).fill(`${IN}/communes.csv`);
  await page.getByLabel("Séparateur", { exact: true }).fill(";");
  await page.getByTestId("node-CsvReader_1").click();
  await quickAdd(page, "Filtre E2E"); // relié automatiquement : Reader.output -> composant.in
  const node = page.getByTestId(`node-${name}_1`);
  await expect(node).toBeVisible();
  await page.getByLabel("Seuil", { exact: true }).fill("1500");
  await node.click();
  await quickAdd(page, "CsvWriter");
  await page.getByLabel("Fichier", { exact: true }).fill(`${OUT}/e2e_composant.csv`);

  await page.getByRole("button", { name: /Lancer/ }).first().click();
  await expect(page.getByTestId("run-status")).toHaveText("done");
  // le nœud composant affiche l'état de son contenu, et ses ports portent les comptes
  await expect(node).toContainText("terminé");
  await expect(page.getByTestId(`port-count-${name}_1-ok`)).toHaveText("5");
  expect(fs.readFileSync(out("e2e_composant.csv"), "utf-8").trim().split("\n")).toHaveLength(6); // en-tête + 5
});

test("composant : publier des versions et les épingler dans un nœud", async ({ page }) => {
  const name = `version_e2e_${Date.now()}`;
  const component = (seuil: string) => ({
    name: "Composant versionné",
    description: "",
    component: { name, label: "Filtre versionné E2E", category: "Composants E2E" },
    published_params: [{ name: "SEUIL", label: "Seuil", default: seuil }],
    nodes: [
      { id: "in", type: "ComponentInput", params: { port: "in" } },
      { id: "t", type: "ExtractByExpression", params: { conditions: ['CAST("pop" AS INTEGER) >= ${SEUIL}'] } },
      { id: "ok", type: "ComponentOutput", params: { port: "ok" } },
    ],
    links: [
      { id: "a", from: ["in", "output"], to: ["t", "input"] },
      { id: "b", from: ["t", "Passed"], to: ["ok", "input"] },
    ],
  });
  const created = await (await page.request.post("/api/flows", { data: { flow: component("1000") } })).json();

  // version 1 publiée depuis le panneau du flux
  await page.goto(`/#flow=${created.id}`);
  await page.getByLabel("Note de version").fill("première");
  await page.getByRole("button", { name: "Publier une version" }).click();
  await expect(page.locator(".version-list")).toContainText("v1");
  await expect(page.locator(".version-list")).toContainText("première");

  // le composant change, version 2 publiée
  const saved = await page.request.put(`/api/flows/${created.id}`, { data: { flow: component("2000"), revision: created.revision } });
  expect(saved.ok()).toBeTruthy();
  expect((await page.request.post(`/api/components/${name}/versions`, { data: { note: "seuil 2000" } })).ok()).toBeTruthy();

  // un nouveau flux : le nœud est épinglé à la dernière version ; on peut en choisir une autre
  await page.goto("/#new");
  await page.reload(); // la palette (dernières versions) est chargée une fois par page : la recharger
  await addFromPalette(page, "CsvReader");
  await page.getByTestId("node-CsvReader_1").click();
  await quickAdd(page, "Filtre versionné E2E");
  const picker = page.getByLabel("Version du composant");
  await expect(picker).toHaveValue("2");
  await picker.selectOption("1");
  await expect(page.getByText("Une version plus récente existe (v2)")).toBeVisible();
  await page.getByRole("button", { name: "Passer à la v2" }).click();
  await expect(picker).toHaveValue("2");
});

test("planification : créer, prévisualiser, lancer et supprimer", async ({ page }) => {
  const created = await page.request.post("/api/flows", {
    data: {
      flow: {
        name: "Flux à planifier",
        published_params: [{ name: "FICHIER", label: "Fichier", default: `${IN}/communes.csv` }],
        nodes: [{ id: "r", type: "Reader", params: { path: "${FICHIER}", delimiter: ";" } }],
        links: [],
      },
    },
  });
  expect(created.ok()).toBeTruthy();
  await page.goto("/#new");
  await chooseMenu(page, "Outils", "Planification");
  const dialog = page.getByRole("dialog", { name: "Planification" });
  await dialog.getByRole("button", { name: "+ Nouvelle planification" }).click();
  await dialog.getByLabel("Nom de la planification").fill("Chaque nuit");
  await dialog.getByLabel("Flux à lancer").selectOption({ label: "Flux à planifier" });
  await expect(dialog.getByLabel("Paramètre FICHIER")).toHaveValue(`${IN}/communes.csv`); // valeur par défaut du flux
  await dialog.getByLabel("Expression cron").fill("pas un cron");
  await expect(dialog.getByTestId("cron-preview")).toContainText("cinq champs");
  await expect(dialog.getByRole("button", { name: "Enregistrer" })).toBeDisabled();
  await dialog.getByRole("button", { name: "Chaque nuit à 2 h" }).click();
  await expect(dialog.getByTestId("cron-preview")).toContainText("Prochaines échéances");
  await dialog.getByRole("button", { name: "Enregistrer" }).click();
  const row = dialog.getByTestId("schedule-row").filter({ hasText: "Chaque nuit" });
  await expect(row).toBeVisible();
  await expect(row).toContainText("0 2 * * *");

  // « Lancer maintenant » crée un run que les workers exécutent
  await row.getByRole("button", { name: "Lancer maintenant" }).click();
  await expect(dialog.getByRole("status").filter({ hasText: "Run lancé" })).toBeVisible();
  await expect(row.getByRole("button", { name: "Dernier run" })).toHaveCount(0); // « maintenant » ne touche pas à la planification

  page.once("dialog", (d) => void d.accept());
  await row.getByRole("button", { name: "Supprimer" }).click();
  await expect(dialog.getByTestId("schedule-row").filter({ hasText: "Chaque nuit" })).toHaveCount(0);
});

test("bandeau du nœud : lancer jusqu'à lui en reprenant le cache, le désactiver ; ses données d'un clic", async ({ page }) => {
  await page.goto("/#new");
  await page.getByLabel("Nom du flux").fill("Flux bandeau");
  await addFromPalette(page, "CsvReader");
  await page.getByLabel("Fichier", { exact: true }).fill(`${IN}/communes.csv`);
  await page.getByLabel("Séparateur", { exact: true }).fill(";");
  const reader = page.getByTestId("node-CsvReader_1");
  const tester = page.getByTestId("node-ExtractByExpression_1");
  await reader.click();
  await quickAdd(page, "ExtractByExpression");
  await page.getByRole("group", { name: "Conditions" }).getByRole("button", { name: "+ Ajouter" }).click();
  await page.getByLabel("Conditions 1").fill('CAST("pop" AS INTEGER) >= 1000');
  await page.getByRole("button", { name: /Lancer/ }).first().click();
  await expect(page.getByTestId("run-status")).toHaveText("done");

  // le nœud sélectionné : contour marqué et bandeau d'actions au-dessus de lui
  await tester.click();
  await expect(tester).toHaveClass(/selected/);
  const runTo = page.getByRole("button", { name: "Lancer jusqu'à ce nœud" });
  await expect(runTo).toBeVisible();
  await expect(runTo).toHaveAttribute("title", /cache/);
  await runTo.click();
  await expect(page.getByTestId("run-status")).toHaveText("done");
  await expect(reader).toHaveClass(/st-cached/); // rien n'a changé : tout vient du cache
  await expect(tester).toHaveClass(/st-cached/);

  // ses données d'un clic sur le nœud (onglet Données affiché, pas de bouton dans le bandeau) ; un clic sur un autre
  // nœud, puis de nouveau sur le même : toujours les bonnes données
  await page.getByRole("tab", { name: /Journal/ }).click();
  await tester.click();
  await expect(page.getByTestId("data-count")).toHaveText("10 entités");
  await reader.click();
  await expect(page.getByTestId("data-count")).toHaveText("20 entités");
  await page.getByTestId("data-row").first().click();
  await reader.click();
  await expect(page.getByTestId("data-count")).toHaveText("20 entités");
  await tester.click();
  await expect(page.getByTestId("data-count")).toHaveText("10 entités");

  // désactiver puis réactiver
  await page.getByRole("button", { name: "Désactiver le nœud" }).click();
  await expect(tester).toHaveClass(/disabled/);
  await expect(page.getByRole("button", { name: "Lancer jusqu'à ce nœud" })).toBeDisabled();
  await page.getByRole("button", { name: "Réactiver le nœud" }).click();
  await expect(tester).not.toHaveClass(/disabled/);
});

test("lecteur limité : indicateur et bulle sur le nœud, seules les premières entités lues", async ({ page }) => {
  await page.goto("/#new");
  await page.getByLabel("Nom du flux").fill("Flux limité");
  await addFromPalette(page, "CsvReader");
  await page.getByLabel("Fichier", { exact: true }).fill(`${IN}/communes.csv`);
  await page.getByLabel("Séparateur", { exact: true }).fill(";");
  const chip = page.getByTestId("row-limit-CsvReader_1");
  await expect(chip).toHaveCount(0); // 0 par défaut : tout est lu, pas d'indicateur

  await page.getByLabel("Lignes lues au plus").fill("5");
  await expect(chip).toHaveText("≤ 5");
  await expect(chip).toHaveAttribute("title", /aux 5 premières entités/);
  await page.getByRole("button", { name: /Lancer/ }).first().click();
  await expect(page.getByTestId("run-status")).toHaveText("done");
  await expect(page.getByTestId("port-count-CsvReader_1-output")).toHaveText("5"); // sur 20
});
