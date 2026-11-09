import { expect, test, type Page } from "@playwright/test";
import { IN } from "./paths";
import { addFromPalette } from "./ui";

/** Flux lecteur → SlowPass (plugin de test : retient chaque lot `seconds` secondes, point sûr toutes les 0,2 s). */
async function openSlowFlow(page: Page, seconds: number): Promise<void> {
  const created = await page.request.post("/api/flows", {
    data: {
      flow: {
        name: `Pause ${Date.now()}`,
        nodes: [
          { id: "r", type: "Reader", params: { path: `${IN}/communes.csv`, delimiter: ";" } },
          { id: "s", type: "SlowPass", params: { seconds } },
        ],
        links: [{ id: "l1", from: ["r", "output"], to: ["s", "input"] }],
      },
    },
  });
  expect(created.ok()).toBeTruthy();
  await page.goto(`/#flow=${(await created.json()).id}`);
  await expect(page.getByTestId("node-s")).toBeVisible();
}

async function pauseWhileRunning(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Lancer", exact: true }).click();
  await expect(page.getByTestId("node-r")).toHaveClass(/st-done/, { timeout: 30_000 }); // le lecteur a fini
  await page.getByTestId("run-pause").click();
  await expect(page.getByTestId("run-status")).toHaveText("paused");
}

test("pause : le run s'arrête au point sûr, puis reprend au même point", async ({ page }) => {
  await openSlowFlow(page, 3);
  await pauseWhileRunning(page);
  await page.waitForTimeout(5_000); // plus long que le nœud lent : sans pause, le run serait fini
  await expect(page.getByTestId("run-status")).toHaveText("paused");
  // le nœud arrêté au point sûr s'affiche « en pause », comme l'inspecteur, pas « en cours »
  const slow = page.getByTestId("node-s");
  await expect(slow).toHaveClass(/st-paused/);
  await expect(slow.locator(".node-foot")).toContainText("en pause");
  await slow.locator(".node-head").click();
  await expect(page.getByTestId("inspector-paused")).toContainText("run en pause");
  await page.getByTestId("run-resume").click();
  await expect(slow).not.toHaveClass(/st-paused/);
  await expect(page.getByTestId("run-status")).toHaveText("done", { timeout: 30_000 });
  await expect(page.getByTestId("node-r")).toHaveClass(/st-done/); // même run : le lecteur n'a pas été relu
});

test("pause : un flux modifié pendant la pause est relancé, le cache des nœuds inchangés repris", async ({ page }) => {
  await openSlowFlow(page, 3);
  await pauseWhileRunning(page);
  await addFromPalette(page, "AddAttributes"); // modification pendant la pause
  await page.getByTestId("run-resume").click();
  await expect(page.getByText("Le flux a changé pendant la pause")).toBeVisible();
  await expect(page.getByTestId("run-status")).toHaveText("done", { timeout: 60_000 });
  await expect(page.getByTestId("node-r")).toHaveClass(/st-cached/); // repris du run arrêté, pas relu
  await expect(page.getByTestId("node-s")).toHaveClass(/st-done/);
});
