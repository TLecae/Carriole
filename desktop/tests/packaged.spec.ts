import { _electron as electron, expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { mainWindow } from "./windows";

// Application EMPAQUETÉE (dossier produit par electron-builder) : Python embarqué, interface, extension DuckDB livrée.
// Lancé avec ETL_PACKAGED_EXE=...\win-unpacked\Carriole.exe ; sauté sinon.
const exe = process.env.ETL_PACKAGED_EXE;

test.skip(!exe, "ETL_PACKAGED_EXE non défini (npm run pack, puis pointer vers release\win-unpacked)");

test.setTimeout(240_000);
test("l'application empaquetée démarre son Python embarqué et exécute un flux géographique hors ligne", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "etl-packaged-"));
  const env = { ...process.env, ETL_HOME: home, PYTHONUTF8: "1" } as Record<string, string>;
  delete env.ELECTRON_RUN_AS_NODE;
  delete env.ETL_PYTHON; // on veut bien le Python embarqué
  // données de Chromium dans le dossier d'essai : une application déjà ouverte sur ce poste ne gêne pas (instance unique)
  const app = await electron.launch({ executablePath: exe!, args: [`--user-data-dir=${path.join(home, "userdata")}`], env });
  try {
    expect(await app.evaluate(({ app: a }) => a.isPackaged)).toBe(true);
    const page = await mainWindow(app);
    await expect(page.getByRole("heading", { name: "Flux récents" })).toBeVisible({ timeout: 120_000 }); // premier démarrage : analyse antivirus

    // le moteur qui répond est celui de l'installation
    const info = JSON.parse(fs.readFileSync(path.join(home, "data", "server.json"), "utf-8"));
    const resources = path.join(path.dirname(exe!), "resources");
    const procs = execFileSync(
      "powershell.exe",
      ["-NoProfile", "-Command", `(Get-Process -Id ${info.pid}).Path`],
      { encoding: "utf-8" },
    ).trim();
    expect(procs.toLowerCase()).toBe(path.join(resources, "runtime", "python", "python.exe").toLowerCase());

    // la CLI embarquée parle au même moteur et exécute un flux qui a besoin de l'extension spatiale (livrée, sans réseau)
    const etl = path.join(resources, "bin", "etl.cmd");
    const data = path.join(home, "zones.csv");
    fs.writeFileSync(data, "id;wkt\n1;POLYGON((0 0,10 0,10 10,0 10,0 0))\n", "utf-8");
    const out = path.join(home, "sortie.csv");
    const flow = {
      name: "aire",
      nodes: [
        { id: "r", type: "Reader", params: { path: data, delimiter: ";", wkt_column: "wkt", crs: "EPSG:2154" } },
        { id: "c", type: "AddAttributes", params: { attributes: [{ name: "aire", expression: "ST_Area(geometry)" }] } },
        { id: "w", type: "Writer", params: { path: out } },
      ],
      links: [
        { id: "a", from: ["r", "output"], to: ["c", "input"] },
        { id: "b", from: ["c", "output"], to: ["w", "input"] },
      ],
    };
    const file = path.join(home, "aire.json");
    fs.writeFileSync(file, JSON.stringify(flow), "utf-8");
    const done = execFileSync("cmd.exe", ["/c", etl, "run", file, "--submit", "--wait"], {
      encoding: "utf-8",
      env: { ...env, ETL_HOME: home },
    });
    expect(done).toContain("Run done");
    expect(fs.readFileSync(out, "utf-8")).toContain("100");
  } finally {
    await app.close();
  }
});
