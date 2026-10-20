import { expect, test } from "@playwright/test";
import { IN } from "./paths";
import { addFromPalette } from "./ui";

const TILE = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);

test("un raster s'affiche sur la carte, réglable, et un clic donne la valeur du pixel", async ({ page }) => {
  const tiles: { status: number; type: string }[] = [];
  const logs: string[] = [];
  await page.route(/\/\/(tile\.openstreetmap\.org|server\.arcgisonline\.com|data\.geopf\.fr)\//, (route) =>
    route.fulfill({ status: 200, contentType: "image/png", body: TILE }),
  );
  page.on("console", (m) => logs.push(`${m.type()}: ${m.text()}`));
  page.on("response", (r) => {
    if (r.url().includes("/tiles/raster/")) tiles.push({ status: r.status(), type: r.headers()["content-type"] ?? "" });
  });
  await page.goto("/#new");
  await page.getByLabel("Nom du flux").fill("Flux raster");
  await addFromPalette(page, "RasterReader");
  await page.getByLabel("Fichier", { exact: true }).fill(`${IN}/mnt.tif`);
  await page.getByRole("button", { name: /Lancer/ }).first().click();
  await expect(page.getByTestId("run-status")).toHaveText("done");

  await page.getByRole("tab", { name: "Données" }).click();
  await page.getByTestId("node-RasterReader_1").click();
  await expect(page.getByTestId("data-count")).toHaveText("1 entités");

  // réglages d'affichage : une bande, une palette, l'étendue automatique
  const controls = page.getByTestId("raster-controls");
  await expect(controls).toBeVisible();
  await expect(controls.getByLabel("Bande")).toHaveValue("1");
  // l'image arrive en tuiles PNG, et change de palette sans erreur
  await expect.poll(() => tiles.length, { message: "tuiles raster" }).toBeGreaterThan(0);
  const before = tiles.length;
  await controls.getByLabel("Palette").selectOption("gray");
  await expect.poll(() => tiles.length, { message: "tuiles après changement de palette" }).toBeGreaterThan(before);
  expect(tiles.filter((t) => !(t.status === 200 && t.type === "image/png"))).toEqual([]);

  // clic au centre de la carte (cadrée sur l'emprise du raster) : valeur du pixel
  const map = page.getByTestId("map");
  await expect(map).toHaveAttribute("data-features", /.+/);
  const box = (await map.boundingBox())!;
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  const popup = page.locator(".feature-popup");
  await expect(popup).toContainText("Pixel : ligne");
  await expect(popup).toContainText("altitude");
  expect(logs.filter((l) => /Content Security Policy/i.test(l))).toEqual([]);
});
