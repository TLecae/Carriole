import { expect, test } from "@playwright/test";
import path from "node:path";
import { IN } from "./paths";
import { addFromPalette, inPalette } from "./ui";

test("accueil : la liste des flux, ouverture et retour", async ({ page }) => {
  const name = `Flux accueil ${Date.now()}`;
  const created = await page.request.post("/api/flows", {
    data: {
      flow: {
        name,
        description: "Prépare les communes de test",
        nodes: [{ id: "r", type: "Reader", params: { path: `${IN}/communes.csv`, delimiter: ";" } }],
        links: [],
      },
    },
  });
  expect(created.ok()).toBeTruthy();
  const id = (await created.json()).id as string;

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Mes flux" })).toBeVisible();
  const card = page.getByTestId("flow-card").filter({ hasText: name });
  await expect(card).toContainText("Prépare les communes de test");
  await expect(card).toContainText("1 nœud");
  await expect(card).toContainText("Jamais lancé");

  // la recherche filtre la liste
  await page.getByLabel("Rechercher un flux").fill("introuvable-xyz");
  await expect(card).toHaveCount(0);
  await page.getByLabel("Rechercher un flux").fill(name);
  await expect(card).toBeVisible();

  // ouvrir : l'éditeur affiche le flux, l'adresse le désigne
  await card.getByRole("button", { name: `Ouvrir ${name}` }).click();
  await expect(page).toHaveURL(new RegExp(`flow=${id}`));
  await expect(page.getByLabel("Nom du flux")).toHaveValue(name);
  await expect(page.getByTestId("node-r")).toBeVisible();

  // retour à l'accueil par le bouton, puis navigation Précédent / Suivant du navigateur
  await page.getByRole("button", { name: /Accueil/ }).click();
  await expect(page.getByRole("heading", { name: "Mes flux" })).toBeVisible();
  await page.goBack();
  await expect(page.getByLabel("Nom du flux")).toHaveValue(name);
  await page.goBack();
  await expect(page.getByRole("heading", { name: "Mes flux" })).toBeVisible();

  // l'adresse d'un flux ouvre directement l'éditeur (lien partagé), sans passer par l'accueil
  await page.goto(`/#flow=${id}`);
  await expect(page.getByTestId("node-r")).toBeVisible();

  await page.request.delete(`/api/flows/${id}`);
});

test("accueil : « Nouveau flux » ouvre un éditeur vide, « Reprendre » le retrouve", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "+ Nouveau flux" }).click();
  await expect(page.getByLabel("Nom du flux")).toHaveValue("Nouveau flux");
  await addFromPalette(page, "CsvReader");
  await expect(page.getByTestId("node-CsvReader_1")).toBeVisible();

  await page.getByRole("button", { name: /Accueil/ }).click();
  await expect(page.getByRole("status").filter({ hasText: "Flux en cours d'édition" })).toBeVisible(); // pas le spinner du chargement
  await page.getByRole("button", { name: "Reprendre" }).click();
  await expect(page.getByTestId("node-CsvReader_1")).toBeVisible(); // rien n'a été perdu
});

test("explorateur de fichiers : choisir un fichier, puis un emplacement d'écriture", async ({ page }) => {
  // Poste de bureau sans restriction : l'explorateur de la page propose le dossier personnel et les lecteurs
  // (l'application de bureau ouvre à la place le dialogue natif). Le refus hors dossiers autorisés (mode serveur) est
  // couvert par api/tests/test_files.py.
  const inDir = path.join(process.env.E2E_ROOT!, "in");
  await page.goto("/#new");
  await addFromPalette(page, "CsvReader");
  await page.getByRole("button", { name: "Fichier : parcourir" }).click();
  const dialog = page.getByRole("dialog", { name: "Choisir un fichier" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Dossier personnel" })).toBeVisible();

  await dialog.getByLabel("Dossier courant").fill(inDir);
  await dialog.getByLabel("Dossier courant").press("Enter");
  await expect(dialog.getByRole("option", { name: /communes\.csv/ })).toBeVisible();
  await dialog.getByRole("option", { name: /communes\.csv/ }).dblclick();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByLabel("Fichier", { exact: true })).toHaveValue(path.join(inDir, "communes.csv"));

  // en écriture, le nom du fichier se saisit
  await page.getByTestId("node-CsvReader_1").click();
  await page.keyboard.press("w");
  await page.getByPlaceholder("Nom du modificateur…").fill("CsvWriter");
  await page.getByPlaceholder("Nom du modificateur…").press("Enter");
  await expect(page.getByTestId("node-CsvWriter_1")).toBeVisible();
  await page.getByRole("button", { name: "Fichier : parcourir" }).click();
  const out = page.getByRole("dialog", { name: "Choisir où écrire" });
  await out.getByLabel("Dossier courant").fill(process.env.E2E_OUT!);
  await out.getByLabel("Dossier courant").press("Enter");
  await out.getByLabel("Nom du fichier").fill("depuis_explorateur.csv");
  await out.getByRole("button", { name: "Choisir" }).click();
  await expect(page.getByLabel("Fichier", { exact: true })).toHaveValue(path.join(process.env.E2E_OUT!, "depuis_explorateur.csv"));
});

test("panneau des propriétés : redimensionner à la souris, largeur conservée", async ({ page }) => {
  await page.goto("/#new");
  const panel = page.getByRole("complementary", { name: "Propriétés" });
  const before = (await panel.boundingBox())!;
  const grip = (await panel.getByRole("separator", { name: /Redimensionner le panneau/ }).boundingBox())!;
  await page.mouse.move(grip.x + grip.width / 2, grip.y + 200);
  await page.mouse.down();
  await page.mouse.move(grip.x - 150, grip.y + 200, { steps: 8 });
  await page.mouse.up();
  expect((await panel.boundingBox())!.width).toBeGreaterThan(before.width + 120);
  await page.reload();
  expect((await panel.boundingBox())!.width).toBeGreaterThan(before.width + 120);
});

test("panneau du bas : redimensionner à la souris et ouvrir en pleine page", async ({ page }) => {
  await page.goto("/#new");
  const panel = page.getByRole("region", { name: "Journal et avertissements" });
  const before = (await panel.boundingBox())!;

  // glisser la poignée vers le haut agrandit le panneau
  const grip = (await panel.getByRole("separator", { name: /Redimensionner le panneau/ }).boundingBox())!;
  await page.mouse.move(grip.x + grip.width / 2, grip.y + grip.height / 2);
  await page.mouse.down();
  await page.mouse.move(grip.x + grip.width / 2, grip.y - 200, { steps: 8 });
  await page.mouse.up();
  expect((await panel.boundingBox())!.height).toBeGreaterThan(before.height + 150);

  // la hauteur choisie est conservée au rechargement
  await page.reload();
  expect((await panel.boundingBox())!.height).toBeGreaterThan(before.height + 150);

  // pleine page : le panneau couvre la fenêtre, Échap revient à l'éditeur
  await page.getByRole("button", { name: /Pleine page/ }).click();
  const full = (await panel.boundingBox())!;
  const viewport = page.viewportSize()!;
  expect(full.height).toBeGreaterThanOrEqual(viewport.height - 1);
  expect(full.width).toBeGreaterThanOrEqual(viewport.width - 1);
  await page.keyboard.press("Escape");
  expect((await panel.boundingBox())!.height).toBeLessThan(full.height);
  await expect(await inPalette(page, "CsvReader")).toBeVisible();
});
