import { expect, test } from "@playwright/test";
import { addFromPalette } from "./ui";

test("les sorties d'un RouteByExpression suivent ses conditions, et ExecuteSql a un éditeur SQL", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "+ Nouveau flux" }).click();
  await addFromPalette(page, "RouteByExpression");
  const node = page.getByTestId("node-RouteByExpression_1");
  await expect(node).toBeVisible();
  await node.click();

  // par défaut une seule sortie « Passed » ; on la renomme et on en ajoute une : le nœud montre ses nouveaux ports
  await expect(node).toContainText("Passed");
  await page.getByLabel("Sortie").first().fill("Grandes");
  await page.getByRole("button", { name: "+ Ajouter une ligne" }).click();
  await page.getByLabel("Sortie").nth(1).fill("Petites");
  await expect(node).toContainText("Grandes");
  await expect(node).toContainText("Petites");
  await expect(node).toContainText("Failed");
  await expect(node).not.toContainText("Passed");

  // un nœud avec un champ de code : éditeur CodeMirror (chargé à la demande), la saisie est enregistrée
  await addFromPalette(page, "ExecuteSql");
  await page.getByTestId("node-ExecuteSql_1").click();
  const editor = page.getByTestId("code-editor");
  await expect(editor).toBeVisible();
  await editor.locator(".cm-content").click();
  await page.keyboard.press("Control+A");
  await page.keyboard.type("SELECT 42 AS reponse");
  await page.getByTestId("node-RouteByExpression_1").click();
  await page.getByTestId("node-ExecuteSql_1").click();
  await expect(editor.locator(".cm-content")).toContainText("SELECT 42 AS reponse");
});
