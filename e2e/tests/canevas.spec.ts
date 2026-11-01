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
