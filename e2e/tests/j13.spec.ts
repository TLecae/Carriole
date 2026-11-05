import { expect, test } from "@playwright/test";
import { IN } from "./paths";
import { addFromPalette } from "./ui";

test("Gérer des attributs : tous les attributs reçus dans un tableau, agrandi en surcouche", async ({ page }) => {
  const flow = {
    name: "gérer des attributs",
    nodes: [
      { id: "lire", type: "CsvReader", position: [0, 0], params: { path: `${IN}/communes.csv`, delimiter: ";" } },
      { id: "gerer", type: "RefactorAttributes", position: [320, 0], params: { rules: [] } },
    ],
    links: [{ id: "l", from: ["lire", "output"], to: ["gerer", "input"] }],
  };
  const created = await (await page.request.post("/api/flows", { data: { flow } })).json();
  await page.goto(`/#flow=${created.id}`);
  await page.getByTestId("node-gerer").click();
  const rows = page.locator(".right-panel .attr-table tbody tr");
  await expect(rows).toHaveCount(4); // id, nom, pop, dep : sans règle, chacun a sa ligne
  await page.getByLabel("Attribut en sortie, ligne 2").fill("nom_commune");
  await expect(rows.nth(1)).toContainText("Renommer");
  await rows.nth(3).getByRole("button", { name: "Supprimer" }).click();
  await expect(rows.nth(3)).toContainText("Garder");
  await page.getByRole("button", { name: "+ Ajouter un attribut" }).click();
  await page.getByLabel("Attribut en sortie, ligne 5").fill("nom_maj");
  await page.getByLabel("Valeur, ligne 5").fill("upper(nom)"); // les valeurs se calculent avant les renommages
  await expect(rows.nth(4)).toContainText("Créer");
  // en surcouche, le même tableau, plus large
  await page.getByRole("button", { name: "Agrandir" }).click();
  const dialog = page.getByRole("dialog", { name: "Gérer des attributs" });
  await expect(dialog.locator(".attr-table tbody tr")).toHaveCount(5);
  await expect(dialog.getByLabel("Attribut en sortie, ligne 2")).toHaveValue("nom_commune");
  await dialog.getByRole("button", { name: "Fermer" }).click();
  // les règles produites : le nœud annonce en sortie les attributs renommés et créés, sans « dep »
  await page.getByTestId("node-gerer").getByRole("button", { name: /attributs/i }).last().click();
  await expect(page.getByTestId("node-gerer")).toContainText("nom_commune");
  await expect(page.getByTestId("node-gerer")).toContainText("nom_maj");
  await expect(page.getByTestId("node-gerer")).not.toHaveClass(/has-error/);
  await expect(page.getByTestId("node-gerer").locator(".port-fields")).not.toContainText("dep");
  await page.request.delete(`/api/flows/${created.id}`);
});

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
