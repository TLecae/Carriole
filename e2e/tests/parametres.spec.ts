import { expect, test } from "@playwright/test";
import { chooseMenu } from "./ui";

test("barre du haut : menus groupés, et paramètres modifiables sans ligne de commande", async ({ page }) => {
  await page.goto("/#new");
  // les actions rares sont dans des menus ; les actions courantes restent visibles
  await expect(page.getByRole("button", { name: "Enregistrer" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Lancer", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Édition", exact: true }).click();
  await expect(page.getByRole("menuitem", { name: "Aligner à gauche" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("menu")).toHaveCount(0);
  await chooseMenu(page, "Outils", "Planification");
  await expect(page.getByRole("dialog", { name: "Planification" })).toBeVisible();
  await page.keyboard.press("Escape");

  // paramètres : réglages enregistrés par le moteur, validés
  await page.getByRole("button", { name: "Paramètres" }).click();
  const dialog = page.getByRole("dialog", { name: "Paramètres" });
  await dialog.getByRole("tab", { name: "Réseau" }).click();
  await dialog.getByLabel("Adresse du serveur de tuiles").fill("pas une adresse");
  await dialog.getByRole("button", { name: "Ajouter" }).click();
  await expect(dialog.locator(".error")).toBeVisible();
  await dialog.getByLabel("Adresse du serveur de tuiles").fill("https://tuiles.e2e.example");
  await dialog.getByRole("button", { name: "Ajouter" }).click();
  await expect(dialog.getByText("https://tuiles.e2e.example")).toBeVisible();
  const state = await (await page.request.get("/api/settings")).json();
  expect(state.values.tile_hosts).toContain("https://tuiles.e2e.example");

  await dialog.getByRole("tab", { name: "Calcul" }).click();
  await dialog.getByLabel("Runs en parallèle").fill("3");
  await dialog.getByLabel("Mémoire maximale du moteur (Mo)").focus();
  await expect(dialog.getByRole("status")).toContainText("prochain démarrage");

  await dialog.getByRole("tab", { name: "Variables globales" }).click();
  await dialog.getByRole("button", { name: "+ Ajouter une variable" }).click();
  await dialog.getByLabel("Nom de la variable 1").fill("RACINE_E2E");
  await dialog.getByLabel("Valeur de la variable 1").fill("D:/donnees");
  await dialog.getByLabel("Valeur de la variable 1").blur();
  await expect.poll(async () => (await (await page.request.get("/api/settings")).json()).values.variables).toEqual({ RACINE_E2E: "D:/donnees" });

  // remise à zéro : les essais ne laissent rien dans les réglages de l'utilisateur
  await page.request.patch("/api/settings", { data: { tile_hosts: null, workers: null, variables: null } });
});
