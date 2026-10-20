import { defineConfig } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

// Les tests visent un vrai backend de bureau (`etl serve`) démarré ici, avec son propre dossier de données.
//   E2E_MODE=db     (défaut) : flux dans la base ; les scénarios de l'éditeur, de l'inspecteur, des runs…
//   E2E_MODE=files            : flux en fichiers (poste de bureau) ; tests/fichiers.spec.ts seulement.
// Les scénarios lisent dans IN et écrivent dans OUT (dossiers temporaires, voir tests/paths.ts).
const pluginCode = [
  "import pyarrow.compute as pc",
  "from etl_engine.sdk import Params, Modifier",
  "",
  "",
  "class DoubleValue(Modifier):",
  '    name = "DoubleValue"',
  '    label = "Double la valeur"',
  '    category = "Calculs"',
  '    description = "Double la colonne v."',
  "",
  "    class Params(Params):",
  "        pass",
  "",
  "    def process(self, batch):",
  '        return {"output": batch.set_column(0, "v", pc.multiply(batch.column("v"), 2))}',
  "",
].join("\n");
const pluginTest = [
  "from etl_engine.testing import run_modifier, table",
  "from plugin_double_value import DoubleValue",
  "",
  "",
  "def test_double():",
  '    assert run_modifier(DoubleValue, {}, table(v=[1, 2]))["output"].column("v").to_pylist() == [2, 4]',
  "",
].join("\n");
const assistantScript = [
  {
    match: "filtre",
    turns: [
      { text: "Je regarde le flux.", tool_calls: [{ name: "get_flow", input: {} }] },
      {
        text: "Je propose d'ajouter un filtre.",
        tool_calls: [
          {
            name: "propose_flow_edits",
            input: {
              explanation: "Garder les lieux dont la population dépasse 5.",
              operations: [
                { op: "add_node", id: "ExtractByExpression_1", type: "ExtractByExpression", params: { conditions: ['"pop" > 5'] } },
                { op: "connect", from: ["CsvReader_1", "output"], to: ["ExtractByExpression_1", "input"] },
              ],
            },
          },
        ],
      },
    ],
  },
  {
    match: "plugin",
    turns: [{ text: "Voici un modificateur.", tool_calls: [{ name: "write_modifier", input: { name: "DoubleValue", code: pluginCode, test_code: pluginTest, explanation: "Double v." } }] }],
  },
  { match: "bonjour", turns: [{ text: "Bonjour ! Que voulez-vous faire ?" }] },
];

const mode = process.env.E2E_MODE === "files" ? "files" : "db";
const root = (process.env.E2E_ROOT ||= fs.mkdtempSync(path.join(os.tmpdir(), "etl-e2e-")));
const port = Number(process.env.E2E_PORT ?? 18080);
const token = "e2e-token";
const repo = path.resolve(here, "..");

const data = { in: path.join(root, "in"), out: path.join(root, "out") };
process.env.E2E_IN = data.in;
process.env.E2E_OUT = data.out;
process.env.E2E_FILES = path.join(root, "flux"); // dossier où les scénarios « fichiers » enregistrent leurs flux
if (!fs.existsSync(path.join(root, "home", "config", "settings.json"))) {
  for (const dir of [...Object.values(data), process.env.E2E_FILES]) fs.mkdirSync(dir, { recursive: true });
  fs.cpSync(path.join(here, "data", "in"), data.in, { recursive: true });
  fs.mkdirSync(path.join(root, "home", "config"), { recursive: true });
  fs.writeFileSync(
    path.join(root, "home", "config", "settings.json"),
    JSON.stringify({
      // assistant : un faux fournisseur scripté (aucun réseau), voir etl_assistant.providers.FakeProvider
      assistant: { default: "fake", providers: { fake: { kind: "fake", label: "Faux fournisseur", model: "fake-1", privacy: "schema", script: assistantScript } } },
    }),
  );
}

const python =
  process.env.ETL_PYTHON ?? path.join(repo, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");

export default defineConfig({
  testDir: "./tests",
  testMatch: mode === "files" ? /fichiers\.spec\.ts/ : /.*\.spec\.ts/,
  testIgnore: mode === "files" ? [] : [/fichiers\.spec\.ts/],
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  webServer: {
    command: `"${python}" -m etl_engine.cli serve --port ${port} --workers 2`,
    url: `http://127.0.0.1:${port}/api/health`,
    timeout: 120_000,
    reuseExistingServer: false,
    stdout: "pipe",
    stderr: "pipe",
    env: {
      ...(process.env as Record<string, string>),
      ETL_HOME: path.join(root, "home"),
      ETL_SERVE_TOKEN: token,
      ETL_FLOW_STORAGE: mode,
      ETL_PLUGINS_DIR: path.join(here, "plugins"),
      ETL_FRONTEND_DIR: path.join(repo, "frontend", "dist"),
      PYTHONUTF8: "1",
    },
  },
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    extraHTTPHeaders: { "X-ETL-Token": token },
    viewport: { width: 1500, height: 900 },
    trace: "retain-on-failure",
    channel: process.env.E2E_CHANNEL ?? "msedge", // Edge est livré avec Windows ; "chromium" si Playwright l'a téléchargé
    // WebGL logiciel pour MapLibre sur un poste sans GPU
    launchOptions: { args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] },
  },
});
