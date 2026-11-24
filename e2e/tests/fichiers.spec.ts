import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { IN } from "./paths";
import { addFromPalette, chooseMenu } from "./ui";

// Mode « fichiers » (poste de bureau) : un flux est un fichier JSON. Lancé avec E2E_MODE=files.
const folder = () => process.env.E2E_FILES!;

/** Dans l'explorateur de la page (l'application de bureau ouvre à la place le dialogue natif du système). */
async function goTo(dialog: ReturnType<Page["getByRole"]>, dir: string) {
  const address = dialog.getByLabel("Dossier courant");
  // le chargement initial de l'explorateur peut terminer après la saisie et la remplacer : on recommence jusqu'à ce que ça tienne
  await expect(async () => {
    await address.fill(dir);
    await address.press("Enter");
    await expect(address).toHaveValue(dir, { timeout: 1500 });
  }).toPass({ timeout: 15_000 });
}

test("enregistrer un flux dans un fichier, le retrouver dans les récents et le rouvrir", async ({ page }) => {
  const file = path.join(folder(), "flux_e2e.json");
  fs.rmSync(file, { force: true });
  await page.goto("/#new");
  // un flux neuf s'appelle « Nouveau flux » et le dit : il n'est pas encore enregistré
  await expect(page.getByTestId("flow-name")).toHaveValue("Nouveau flux");
  await expect(page.getByTestId("flow-unsaved")).toBeVisible();
  await addFromPalette(page, "CsvReader");
  await page.getByLabel("Fichier", { exact: true }).fill(`${IN}/communes.csv`);
  await page.getByLabel("Séparateur", { exact: true }).fill(";");

  // Premier enregistrement : l'emplacement est demandé
  await page.getByTestId("node-CsvReader_1").click();
  await page.keyboard.press("Control+s");
  const dialog = page.getByRole("dialog", { name: "Enregistrer le flux" });
  await expect(dialog).toBeVisible();
  await goTo(dialog, folder());
  await dialog.getByLabel("Nom du fichier").fill("flux_e2e.json");
  await dialog.getByRole("button", { name: "Choisir" }).click();
  await expect(page.getByText("Flux enregistré")).toBeVisible();
  await expect(page).toHaveURL(/flow=[A-Za-z0-9_-]+/);
  // le flux prend le nom de son fichier
  expect(JSON.parse(fs.readFileSync(file, "utf-8")).name).toBe("flux_e2e");
  await expect(page.getByTestId("flow-name")).toHaveValue("flux_e2e");
  await expect(page.getByTestId("flow-unsaved")).toHaveCount(0);

  // Enregistrements suivants : directement dans le même fichier, sans question
  await page.locator(".react-flow__pane").click({ position: { x: 600, y: 400 } });
  await page.getByRole("button", { name: "Propriétés du flux" }).click(); // sans sélection, le panneau est replié
  await page.getByLabel("Description").fill("communes lues dans un CSV");
  await page.getByRole("button", { name: "Enregistrer", exact: false }).first().click();
  // le toast du premier enregistrement peut encore être affiché : on attend le contenu du fichier
  await expect.poll(() => JSON.parse(fs.readFileSync(file, "utf-8")).description).toBe("communes lues dans un CSV");
  expect(JSON.parse(fs.readFileSync(file, "utf-8")).name).toBe("flux_e2e");

  // Le flux s'exécute (le worker le lit comme un flux ordinaire)
  await page.getByRole("button", { name: /Lancer/ }).first().click();
  await expect(page.getByTestId("run-status")).toHaveText("done");

  // Accueil : liste des flux récents (et non « Mes flux »), avec l'emplacement
  await page.getByRole("button", { name: /Accueil/ }).click();
  await expect(page.getByRole("heading", { name: "Flux récents" })).toBeVisible();
  const card = page.getByTestId("flow-card").filter({ hasText: "flux_e2e" });
  await expect(card).toContainText("flux_e2e.json");
  await expect(card.locator(".run-chip")).toHaveText("terminé"); // le dernier run est retrouvé par l'identifiant du fichier

  // Retirer de la liste ne supprime jamais le fichier
  await card.getByRole("button", { name: /Retirer/ }).click();
  await expect(card).toHaveCount(0);
  expect(fs.existsSync(file)).toBe(true);

  // Ouvrir un flux… : explorateur limité aux fichiers .json
  await page.getByRole("button", { name: "Ouvrir un flux…" }).click();
  const open = page.getByRole("dialog", { name: "Ouvrir un flux" });
  await goTo(open, folder());
  await open.getByRole("option", { name: /flux_e2e\.json/ }).click();
  await open.getByRole("button", { name: "Choisir" }).click();
  await expect(page.getByTestId("flow-name")).toHaveValue("flux_e2e");
  await expect(page.getByTestId("node-CsvReader_1")).toBeVisible();
});

test("un collègue a modifié le fichier : l'enregistrement est refusé, rien n'est écrasé", async ({ page }) => {
  const file = path.join(folder(), "partage_e2e.json");
  const created = await page.request.post("/api/flows", {
    data: { flow: { name: "Flux partagé", nodes: [], links: [] }, path: file },
  });
  expect(created.ok()).toBeTruthy();
  const id = (await created.json()).id as string;

  await page.goto(`/#flow=${id}`);
  await expect(page.getByTestId("flow-name")).toHaveValue("partage_e2e"); // le nom du fichier
  // le collègue enregistre de son côté
  const theirs = JSON.parse(fs.readFileSync(file, "utf-8"));
  fs.writeFileSync(file, JSON.stringify({ ...theirs, description: "Version du collègue" }));

  await addFromPalette(page, "CsvReader");
  await page.keyboard.press("Control+s");
  await expect(page.getByText(/modifié par quelqu'un d'autre/)).toBeVisible();
  expect(JSON.parse(fs.readFileSync(file, "utf-8")).description).toBe("Version du collègue");

  // « Enregistrer sous… » garde mon travail dans un autre fichier
  await chooseMenu(page, "Fichier", "Enregistrer sous…");
  const dialog = page.getByRole("dialog", { name: "Enregistrer le flux" });
  await goTo(dialog, folder());
  await dialog.getByLabel("Nom du fichier").fill("ma_version.json");
  await dialog.getByRole("button", { name: "Choisir" }).click();
  await expect(page.getByText("Flux enregistré")).toBeVisible();
  const mine = JSON.parse(fs.readFileSync(path.join(folder(), "ma_version.json"), "utf-8"));
  expect(mine.name).toBe("ma_version");
  expect(mine.nodes).toHaveLength(1);
  await expect(page.getByTestId("flow-name")).toHaveValue("ma_version");
});

test("nom du flux changé : nouveau fichier par défaut, ou renommage du fichier", async ({ page }) => {
  const file = (name: string) => path.join(folder(), `${name}.json`);
  for (const name of ["nom_e2e", "nom_e2e_copie", "nom_e2e_final"]) fs.rmSync(file(name), { force: true });
  const created = await page.request.post("/api/flows", { data: { flow: { name: "x", nodes: [], links: [] }, path: file("nom_e2e") } });
  await page.goto(`/#flow=${(await created.json()).id}`);
  await expect(page.getByTestId("flow-name")).toHaveValue("nom_e2e");

  const save = async (name: string) => {
    await page.getByTestId("flow-name").fill(name);
    await page.getByRole("button", { name: "Enregistrer", exact: false }).first().click();
    const dialog = page.getByRole("dialog", { name: "Nom du flux modifié" });
    await expect(dialog).toBeVisible();
    return dialog;
  };
  // choix par défaut : un nouveau fichier à côté, l'original reste
  const first = await save("nom_e2e_copie");
  await expect(first.getByRole("button", { name: "Créer un nouveau fichier" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect.poll(() => fs.existsSync(file("nom_e2e_copie"))).toBe(true);
  expect(fs.existsSync(file("nom_e2e"))).toBe(true);
  await expect(page.getByTestId("flow-name")).toHaveValue("nom_e2e_copie");

  // renommer : le fichier change de nom
  const second = await save("nom_e2e_final");
  await second.getByRole("button", { name: "Renommer le fichier" }).click();
  await expect.poll(() => fs.existsSync(file("nom_e2e_final"))).toBe(true);
  expect(fs.existsSync(file("nom_e2e_copie"))).toBe(false);
  await expect(page.getByTestId("flow-name")).toHaveValue("nom_e2e_final");
});
