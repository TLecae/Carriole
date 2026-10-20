import { expect, test } from "@playwright/test";
import fs from "node:fs";
import { IN, OUT } from "./paths";
import { addFromPalette, chooseMenu } from "./ui";

test("lot : un run par fichier du motif, regroupés dans la liste des runs", async ({ page }) => {
  const name = `Lot ${Date.now()}`;
  const created = await page.request.post("/api/flows", {
    data: {
      flow: {
        name,
        published_params: [{ name: "SRC", label: "Fichier source", type: "path", default: `${IN}/communes.csv`, required: true }],
        variables: { SORTIE: `${OUT}/lot_` + "${sys:run_id}.csv" },
        nodes: [
          { id: "r", type: "Reader", params: { path: "${SRC}", delimiter: ";" } },
          { id: "w", type: "Writer", params: { path: "${SORTIE}", format: "csv" } },
        ],
        links: [{ id: "l", from: ["r", "output"], to: ["w", "input"] }],
      },
    },
  });
  expect(created.ok()).toBeTruthy();
  const id = (await created.json()).id as string;

  await page.goto(`/#flow=${id}`);
  await expect(page.getByTestId("node-r")).toBeVisible();
  await chooseMenu(page, "Exécution", "En lot…");
  await page.getByLabel("Motif de fichiers").fill(`${IN}/*.csv`);
  await expect(page.getByTestId("batch-count")).toContainText("2 run(s)");
  await page.getByRole("button", { name: "Lancer le lot" }).click();

  // la liste des runs s'ouvre : deux runs du lot, étiquetés
  const rows = page.getByTestId("run-row").filter({ hasText: name });
  await expect(rows).toHaveCount(2);
  await expect(rows.first()).toContainText(/lot \d\/2/);

  // les deux runs se terminent et écrivent chacun leur fichier (${sys:run_id} dans le chemin)
  await expect
    .poll(async () => (await (await page.request.get("/api/runs")).json()).filter((r: { flow_name: string; status: string }) => r.flow_name === name && r.status === "done").length)
    .toBe(2);
  const out = process.env.E2E_OUT!;
  expect(fs.readdirSync(out).filter((f) => f.startsWith("lot_")).length).toBe(2);
  await page.request.delete(`/api/flows/${id}`);
});

test("variables : paramètres, variables du flux et valeur résolue affichée sous le champ", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "+ Nouveau flux" }).click();
  await addFromPalette(page, "CsvReader");
  await page.getByTestId("node-CsvReader_1").click();
  // panneau du flux : une variable composée
  await page.locator(".react-flow__pane").click({ position: { x: 600, y: 400 } });
  await page.getByRole("button", { name: "+ Ajouter une variable" }).click();
  await page.getByLabel("Nom de la variable 1").fill("RACINE");
  await page.getByLabel("Valeur de la variable 1").fill(IN);
  await page.getByTestId("node-CsvReader_1").click();
  await page.getByLabel("Fichier", { exact: true }).fill("${RACINE}/communes.csv");
  await expect(page.locator(".var-preview")).toContainText(`→ ${IN}/communes.csv`);
  // le sélecteur insère une variable connue
  await page.getByLabel("Insérer une variable").first().selectOption("sys:date");
  await expect(page.getByLabel("Fichier", { exact: true })).toHaveValue(/\$\{sys:date\}/);
});
