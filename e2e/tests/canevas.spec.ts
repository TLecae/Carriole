import { expect, test, type Locator, type Page } from "@playwright/test";
import { IN } from "./paths";
import { addFromPalette } from "./ui";

const edge = (page: Page, from: string, to: string) => page.locator(`.react-flow__edge[aria-label="Edge from ${from} to ${to}"]`);

/** Glisse un nœud par son en-tête jusqu'au point donné (coordonnées de la page). */
async function dragHead(page: Page, node: Locator, to: { x: number; y: number }) {
  const head = (await node.locator(".node-head").boundingBox())!;
  await page.mouse.move(head.x + head.width / 2, head.y + head.height / 2);
  await page.mouse.down();
  await page.mouse.move(to.x, to.y, { steps: 12 });
  await page.mouse.up();
}

test("canevas : brancher par clics, intercaler sur une liaison, attributs des ports, annotations liées, carte", async ({ page }) => {
  await page.goto("/#new");
  await page.getByLabel("Nom du flux").fill("Flux canevas");
  await addFromPalette(page, "CsvReader");
  await page.getByLabel("Fichier", { exact: true }).fill(`${IN}/communes.csv`);
  await page.getByLabel("Séparateur", { exact: true }).fill(";");
  await addFromPalette(page, "CsvWriter");
  const reader = page.getByTestId("node-CsvReader_1");
  const writer = page.getByTestId("node-CsvWriter_1");
  await expect(page.locator(".react-flow__edge")).toHaveCount(0);

  // brancher par clics : le bouton du bandeau, puis le nœud qui reçoit (Échap annule)
  await reader.locator(".node-head").click();
  await page.getByRole("button", { name: "Brancher à un autre modificateur" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Cliquez sur le modificateur" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.locator(".pick-hint")).toHaveCount(0);
  await reader.locator(".node-head").click();
  await page.getByRole("button", { name: "Brancher à un autre modificateur" }).click();
  await writer.locator(".node-head").click();
  await expect(edge(page, "CsvReader_1", "CsvWriter_1")).toHaveCount(1);

  // un nœud libre lâché sur la liaison s'y intercale
  await addFromPalette(page, "ExtractByExpression");
  const tester = page.getByTestId("node-ExtractByExpression_1");
  await page.getByRole("button", { name: "fit view" }).click();
  const line = (await edge(page, "CsvReader_1", "CsvWriter_1").locator(".react-flow__edge-path").boundingBox())!;
  await dragHead(page, tester, { x: line.x + line.width / 2, y: line.y + line.height / 2 });
  await expect(edge(page, "CsvReader_1", "CsvWriter_1")).toHaveCount(0);
  await expect(edge(page, "CsvReader_1", "ExtractByExpression_1")).toHaveCount(1);
  await expect(edge(page, "ExtractByExpression_1", "CsvWriter_1")).toHaveCount(1);
  await page.getByRole("button", { name: "Annuler" }).click(); // un seul pas : déplacement et liaisons
  await expect(edge(page, "CsvReader_1", "CsvWriter_1")).toHaveCount(1);
  await page.getByRole("button", { name: "Rétablir" }).click();
  await expect(edge(page, "ExtractByExpression_1", "CsvWriter_1")).toHaveCount(1);

  // attributs d'un port de sortie, derrière un chevron
  await page.getByTestId("port-toggle-CsvReader_1-output").click();
  await expect(page.getByTestId("port-fields-CsvReader_1-output")).toContainText("pop");
  await page.getByTestId("port-toggle-CsvReader_1-output").click();
  await expect(page.getByTestId("port-fields-CsvReader_1-output")).toHaveCount(0);

  // annotation liée : créée depuis le bandeau, elle suit son nœud
  await tester.locator(".node-head").click();
  await page.getByRole("button", { name: "Annoter le nœud" }).click();
  const note = page.getByTestId("note-note_1");
  await expect(note).toHaveClass(/linked/);
  const noteBefore = (await note.boundingBox())!;
  const nodeBefore = (await tester.boundingBox())!;
  await dragHead(page, tester, { x: nodeBefore.x + nodeBefore.width / 2 + 60, y: nodeBefore.y + 15 + 40 });
  const nodeAfter = (await tester.boundingBox())!;
  expect(nodeAfter.x - nodeBefore.x).toBeGreaterThan(30); // aimanté à la grille : à peu près 60 px
  await expect.poll(async () => Math.round((await note.boundingBox())!.x - noteBefore.x)).toBe(Math.round(nodeAfter.x - nodeBefore.x));
  // délier, puis lier à un autre modificateur par clic (vue recadrée : le bandeau de l'annotation reste visible)
  await page.getByRole("button", { name: "fit view" }).click();
  await page.getByRole("button", { name: "zoom out" }).click();
  await note.click({ position: { x: 4, y: 4 } });
  await page.getByRole("button", { name: "Délier l'annotation" }).click();
  await expect(note).not.toHaveClass(/linked/);
  await page.getByRole("button", { name: "Lier à un modificateur" }).click();
  await reader.locator(".node-head").dispatchEvent("click"); // l'annotation et le nœud intercalé le recouvrent en partie
  await expect(note).toHaveClass(/linked/);

  // le lien est enregistré avec le flux
  await page.keyboard.press("Control+s");
  await expect(page.getByText("Flux enregistré")).toBeVisible();
  await page.reload();
  await expect(page.getByTestId("note-note_1")).toHaveClass(/linked/);

  // la carte de situation se replie, et le reste au rechargement
  await expect(page.locator(".react-flow__minimap")).toBeVisible();
  await page.getByTestId("minimap-toggle").click();
  await expect(page.locator(".react-flow__minimap")).toHaveCount(0);
  await page.reload();
  await expect(page.getByTestId("node-CsvWriter_1")).toBeVisible();
  await expect(page.locator(".react-flow__minimap")).toHaveCount(0);
  await page.getByTestId("minimap-toggle").click();
  await expect(page.locator(".react-flow__minimap")).toBeVisible();
});

test("réordonner des attributs : flèches dans le formulaire, ordre du port de sortie", async ({ page }) => {
  await page.goto("/#new");
  await addFromPalette(page, "CsvReader");
  await page.getByLabel("Fichier", { exact: true }).fill(`${IN}/communes.csv`);
  await page.getByLabel("Séparateur", { exact: true }).fill(";");
  await addFromPalette(page, "ReorderAttributes");
  await page.getByTestId("node-CsvReader_1").locator(".node-head").click();
  await page.getByRole("button", { name: "Brancher à un autre modificateur" }).click();
  await page.getByTestId("node-ReorderAttributes_1").locator(".node-head").click();
  await expect(edge(page, "CsvReader_1", "ReorderAttributes_1")).toHaveCount(1);

  await page.getByTestId("node-ReorderAttributes_1").locator(".node-head").click();
  const order = page.getByRole("list", { name: "Ordre" });
  await expect(order.getByRole("listitem")).toHaveText([/id/, /nom/, /pop/, /dep/]);
  await order.getByRole("button", { name: "Monter pop" }).click();
  await order.getByRole("button", { name: "Monter pop" }).click();
  await expect(order.getByRole("listitem")).toHaveText([/pop/, /id/, /nom/, /dep/]);
  await page.getByTestId("port-toggle-ReorderAttributes_1-output").click();
  await expect(page.getByTestId("port-fields-ReorderAttributes_1-output").locator("li").first()).toContainText("pop");
  await order.getByRole("button", { name: "Remettre pop dans l'ordre reçu" }).click();
  await expect(order.getByRole("listitem")).toHaveText([/id/, /nom/, /pop/, /dep/]);
});

test("port retiré par un paramètre : sa liaison reste visible, barrée, et se supprime", async ({ page }) => {
  await page.goto("/#new");
  await addFromPalette(page, "CsvReader");
  await page.getByLabel("Fichier", { exact: true }).fill(`${IN}/communes.csv`);
  await addFromPalette(page, "CreateGrid");
  await page.getByLabel("Emprise des entités reçues", { exact: true }).check();
  const grid = page.getByTestId("node-CreateGrid_1");
  await expect(grid.locator('.react-flow__handle[data-handleid="input"]')).toBeVisible();
  await page.getByTestId("node-CsvReader_1").locator(".node-head").click();
  await page.getByRole("button", { name: "Brancher à un autre modificateur" }).click();
  await grid.locator(".node-head").click();
  await expect(edge(page, "CsvReader_1", "CreateGrid_1")).toHaveCount(1);

  // l'entrée disparaît avec le paramètre : la liaison reste dessinée, vers un port barré
  await grid.locator(".node-head").click();
  await page.getByLabel("Emprise des entités reçues", { exact: true }).uncheck();
  await expect(page.getByTestId("orphan-CreateGrid_1-input")).toBeVisible();
  await expect(edge(page, "CsvReader_1", "CreateGrid_1")).toHaveCount(1);
  await edge(page, "CsvReader_1", "CreateGrid_1").click({ force: true });
  await page.keyboard.press("Delete");
  await expect(edge(page, "CsvReader_1", "CreateGrid_1")).toHaveCount(0);
  await expect(page.getByTestId("orphan-CreateGrid_1-input")).toHaveCount(0);
});

/** Glisse d'une poignée de port à une autre. */
async function connect(page: Page, from: Locator, to: Locator) {
  const a = (await from.boundingBox())!;
  const b = (await to.boundingBox())!;
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 10 });
  await page.mouse.up();
}

test("filtre à plusieurs sorties : chaque sortie se branche, renommée elle garde sa liaison", async ({ page }) => {
  await page.goto("/#new");
  for (let i = 0; i < 3; i++) await addFromPalette(page, "CsvWriter");
  await addFromPalette(page, "RouteByExpression");
  const router = page.getByTestId("node-RouteByExpression_1");
  const outputs = page.getByLabel("Sortie", { exact: true });
  const conditions = page.getByLabel("Condition", { exact: true });
  const names = ["Petites", "Moyennes", "Grandes"];
  for (const [i, name] of names.entries()) {
    if (i) await page.getByRole("button", { name: "+ Ajouter une ligne" }).click();
    await outputs.nth(i).fill("");
    await outputs.nth(i).pressSequentially(name); // frappe par frappe : le nœud garde sa taille d'un nom à l'autre
    await conditions.nth(i).fill(`"pop" > ${i}`);
  }
  await page.getByRole("button", { name: "fit view" }).click();
  const handle = (node: Locator, port: string) => node.locator(`.react-flow__handle[data-handleid="${port}"]`);
  for (const [i, name] of names.entries()) {
    await expect(handle(router, name)).toBeVisible();
    await connect(page, handle(router, name), handle(page.getByTestId(`node-CsvWriter_${i + 1}`), "input"));
    await expect(edge(page, "RouteByExpression_1", `CsvWriter_${i + 1}`)).toHaveCount(1);
  }

  // renommer une sortie (d'un coup, ou en la vidant puis en la ressaisissant) : la liaison suit, sans erreur
  await router.locator(".node-head").click();
  await outputs.nth(1).fill("Moyennes_bis");
  await outputs.nth(2).fill("");
  await outputs.nth(2).pressSequentially("Enormes");
  await expect(handle(router, "Enormes")).toBeVisible();
  await expect(page.locator(".react-flow__edge")).toHaveCount(3);
  await expect(router).not.toHaveClass(/has-error/);
  await page.getByRole("tab", { name: /Avertissements/ }).click();
  await expect(page.getByText(/n'a pas de sortie/)).toHaveCount(0);
});
