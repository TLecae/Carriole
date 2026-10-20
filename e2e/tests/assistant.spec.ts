import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { IN } from "./paths";
import { addFromPalette, inPalette } from "./ui";

const here = path.dirname(fileURLToPath(import.meta.url));

test.afterAll(() => {
  fs.rmSync(path.join(here, "..", "plugins", "double_value.py"), { force: true }); // le modificateur installé par le scénario
});

test("l'assistant lit le flux, propose une modification qu'on applique d'un clic (annulable), et ne change rien sans clic", async ({ page }) => {
  await page.goto("/#new");
  await addFromPalette(page, "CsvReader");
  await page.getByLabel("Fichier", { exact: true }).fill(`${IN}/lieux.csv`);
  await expect(page.getByTestId("node-CsvReader_1")).toBeVisible();

  await page.getByTestId("open-assistant").click();
  const panel = page.getByTestId("assistant");
  await expect(panel).toBeVisible();
  // confidentialité visible : un fournisseur distant ne reçoit que des schémas
  await expect(page.getByTestId("ai-privacy")).toHaveAttribute("data-mode", "schema");
  await expect(page.getByTestId("ai-privacy")).toContainText("Schémas seulement");

  // références @ : les nœuds du flux sont proposés
  const box = panel.getByLabel("Message à l'assistant");
  await box.fill("@Rea");
  await expect(panel.getByRole("option", { name: /CsvReader_1/ }).first()).toBeVisible();
  await box.fill("");

  await box.fill("ajoute un filtre sur la population");
  await page.getByTestId("ai-send").click();
  await expect(page.getByTestId("ai-user")).toContainText("ajoute un filtre");
  await expect(page.getByTestId("ai-assistant").first()).toContainText("Je regarde le flux.");
  const action = page.getByTestId("ai-action");
  await expect(action).toContainText("Ajouter ExtractByExpression");
  await expect(action).toContainText("Relier CsvReader_1.output → ExtractByExpression_1.input");
  // rien n'est appliqué avant le clic
  await expect(page.getByTestId("node-ExtractByExpression_1")).toHaveCount(0);

  await page.getByTestId("ai-apply").click();
  await expect(action).toHaveAttribute("data-status", "applied");
  await expect(page.getByTestId("node-ExtractByExpression_1")).toBeVisible();

  // une seule modification annulable
  await page.getByRole("button", { name: "Annuler", exact: true }).click();
  await expect(page.getByTestId("node-ExtractByExpression_1")).toHaveCount(0);

  // la conversation est conservée et le sort de la proposition est transmis au modèle au message suivant
  await box.fill("bonjour");
  await page.getByTestId("ai-send").click();
  await expect(panel).toContainText("Bonjour ! Que voulez-vous faire ?");
  await expect(panel.getByLabel("Conversations")).not.toHaveValue("");
});

test("un modificateur écrit par l'assistant est relu, testé en isolation, installé et apparaît dans la palette", async ({ page }) => {
  await page.goto("/#new");
  await page.getByTestId("open-assistant").click();
  const panel = page.getByTestId("assistant");
  await panel.getByLabel("Message à l'assistant").fill("écris un plugin qui double la valeur v");
  await page.getByTestId("ai-send").click();
  const action = page.getByTestId("ai-action");
  await expect(action).toContainText("Nouveau modifier");
  await action.getByText("Code du modificateur").click();
  await expect(action).toContainText("class DoubleValue(Modifier)");
  await expect(page.getByTestId("palette-DoubleValue")).toHaveCount(0); // pas installé avant le clic
  await page.getByTestId("ai-apply").click();
  await expect(action).toHaveAttribute("data-status", "applied", { timeout: 60_000 });
  await expect(await inPalette(page, "DoubleValue")).toBeVisible();
});

test("réglages de l'assistant : fournisseurs, clé jamais affichée, confidentialité par défaut", async ({ page }) => {
  await page.goto("/#new");
  await page.getByTestId("open-assistant").click();
  await page.getByRole("button", { name: "Réglages de l'assistant" }).click();
  const dialog = page.getByRole("dialog", { name: "Fournisseurs de l'assistant" });
  await expect(dialog).toContainText("Faux fournisseur");
  await dialog.getByRole("button", { name: /Ollama/ }).click();
  const form = page.getByRole("dialog", { name: "Nouveau fournisseur" });
  // un modèle sur ce poste peut recevoir des lignes d'exemple ; un fournisseur distant jamais par défaut
  await expect(form.getByLabel("Données transmises au modèle")).toHaveValue("sample");
  await form.getByRole("button", { name: "Retour" }).click();
  await dialog.getByRole("button", { name: /OpenRouter/ }).click();
  const remote = page.getByRole("dialog", { name: "Nouveau fournisseur" });
  await expect(remote.getByLabel("Données transmises au modèle")).toHaveValue("schema");
  await remote.getByLabel("Clé d'API").fill("sk-or-secret-de-test");
  await remote.getByRole("button", { name: "Enregistrer" }).click();
  await expect(dialog).toContainText("OpenRouter");
  await dialog.getByRole("button", { name: "Modifier" }).last().click();
  const again = page.getByRole("dialog", { name: /OpenRouter/ });
  await expect(again.getByLabel("Clé d'API")).toHaveValue(""); // la clé ne revient jamais à l'écran
  await expect(again.getByLabel("Clé d'API")).toHaveAttribute("placeholder", /enregistrée/);
  expect(await page.content()).not.toContain("sk-or-secret-de-test");
});
