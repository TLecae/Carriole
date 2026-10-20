import { expect, test } from "@playwright/test";
import { addFromPalette, inPalette } from "./ui";

test("langue : l'interface passe en anglais depuis les paramètres, avec la palette, les erreurs et le réglage", async ({ page }) => {
  try {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Mes flux" })).toBeVisible();

    // le français est la langue par défaut, quelle que soit celle du navigateur
    await page.getByRole("button", { name: "Paramètres" }).click();
    await page.getByLabel("Langue de l'interface").selectOption("en");
    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
    await page.getByRole("button", { name: "Close" }).click();
    await expect(page.getByRole("heading", { name: "My flows" })).toBeVisible();

    // un flux en anglais : palette, avertissements d'analyse et message d'erreur traduits
    await page.getByRole("button", { name: "+ New flow" }).click();
    await expect(page.getByLabel("Flow name")).toHaveValue("New flow");
    await page.getByLabel("Search for a modifier").fill("Extract by");
    await expect(await inPalette(page, "ExtractByExpression")).toHaveText("Extract by expression");
    await addFromPalette(page, "ExtractByExpression");
    await page.getByRole("tab", { name: /Warnings/ }).click();
    await expect(page.getByRole("listitem").filter({ hasText: "at least one condition is required" })).toBeVisible();

    // le choix est un réglage de l'utilisateur : il survit au rechargement de la page
    await page.reload();
    await expect(page.getByLabel("Flow name")).toBeVisible();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeVisible();
  } finally {
    await page.request.put("/api/preferences/language", { data: { language: "fr" } });
  }
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Mes flux" })).toBeVisible();
});
