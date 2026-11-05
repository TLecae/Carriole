import { expect, test } from "@playwright/test";
import { IN } from "./paths";
import { addFromPalette } from "./ui";

test("éditeur : attributs et valeurs à insérer, depuis le fichier lu ou le dernier run", async ({ page }) => {
  const flow = {
    name: "aide à la saisie",
    nodes: [
      { id: "lire", type: "CsvReader", position: [0, 0], params: { path: `${IN}/communes.csv`, delimiter: ";" } },
      { id: "filtre", type: "ExtractByExpression", position: [320, 0], params: { conditions: [""] } },
    ],
    links: [{ id: "l", from: ["lire", "output"], to: ["filtre", "input"] }],
  };
  const created = await (await page.request.post("/api/flows", { data: { flow } })).json();
  await page.goto(`/#flow=${created.id}`);

  // requête de lecture du lecteur : SELECT (attributs cochés), WHERE avec les colonnes du fichier et leurs valeurs,
  // lues dans le fichier (sans run), et le nombre d'entités gardées
  await page.getByTestId("node-lire").click();
  await expect(page.getByRole("textbox", { name: "Filtre (WHERE)" })).toHaveCount(0); // un seul champ : la requête
  await page.getByRole("button", { name: "Requête de lecture" }).click();
  const reading = page.getByRole("dialog", { name: "Requête de lecture" });
  const file = reading.getByRole("complementary", { name: "Attributs et valeurs" });
  await expect(reading.locator(".cm-content")).toBeVisible(); // éditeur chargé (à la demande)
  await file.getByRole("button", { name: "dep", exact: true }).click(); // insère "dep"
  await reading.locator(".cm-content").press("End");
  await page.keyboard.type(" = ");
  await file.getByRole("button", { name: "Valeurs de dep" }).click();
  await file.getByRole("button", { name: /^31/ }).click(); // insère '31'
  await expect(reading.locator(".cm-content")).toHaveText(`"dep" = '31'`);
  await expect(file).toContainText("Valeurs lues dans le fichier.");
  await expect(reading.getByTestId("rq-select")).toHaveText("* (tous les attributs)");
  await file.getByRole("checkbox", { name: "Lire id" }).uncheck();
  await expect(reading.getByTestId("rq-select")).toHaveText(`"nom", "pop", "dep"`);
  await expect(file).toContainText("3 attributs lus sur 4");
  await reading.getByRole("button", { name: "Compter les entités" }).click();
  await expect(reading.locator(".sql-result")).toHaveText("10 entités gardées sur 20");
  await reading.getByRole("button", { name: "Fermer" }).click();
  await expect(page.getByTestId("read-where")).toHaveText(`"dep" = '31'`);
  await expect(page.getByTestId("read-select")).toHaveText("nom, pop, dep");
  await page.getByRole("button", { name: "Requête de lecture" }).click();
  await file.getByRole("button", { name: "Tous", exact: true }).click();
  await reading.getByRole("button", { name: "Fermer" }).click();
  await expect(page.getByTestId("read-select")).toHaveText("tous les attributs");
  const editor = page.getByRole("dialog", { name: /Éditeur SQL/ });
  const helper = editor.getByRole("complementary", { name: "Attributs et valeurs" });

  // condition d'un filtre : les attributs de l'entrée, leurs valeurs après un run
  await page.getByTestId("node-filtre").click();
  await page.getByRole("button", { name: "Éditeur SQL" }).first().click();
  await helper.getByRole("button", { name: "Valeurs de nom" }).click();
  await expect(helper).toContainText("lancer le flux");
  await editor.getByRole("button", { name: "Fermer" }).click();
  await page.getByLabel("Conditions 1").fill("pop IS NOT NULL");
  await page.getByRole("button", { name: /Lancer/ }).first().click();
  await expect(page.getByTestId("run-status")).toHaveText("done");
  await page.getByTestId("node-filtre").click();
  await page.getByRole("button", { name: "Éditeur SQL" }).first().click();
  await helper.getByRole("button", { name: "Valeurs de dep" }).click();
  await expect(helper).toContainText("Valeurs du dernier run.");
  await expect(helper.getByRole("button", { name: /^31/ })).toBeVisible(); // le lecteur ne garde que le 31
  await expect(helper.getByRole("button", { name: /^09/ })).toHaveCount(0);
  await page.request.delete(`/api/flows/${created.id}`);
});

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

  // éditeur SQL en surcouche : même texte, une erreur de syntaxe est signalée par le serveur (DuckDB)
  await page.getByRole("button", { name: "Éditeur SQL" }).click();
  const overlay = page.getByRole("dialog", { name: /Éditeur SQL/ });
  await expect(overlay).toContainText("DuckDB");
  await expect(overlay.locator(".cm-content")).toContainText("SELECT 42 AS reponse");
  await overlay.locator(".cm-content").click();
  await page.keyboard.press("Control+End");
  await page.keyboard.type(" FORM t");
  await overlay.getByRole("button", { name: "Vérifier" }).click();
  await expect(overlay.locator(".sql-result")).toContainText("Ligne 1");
  await expect(overlay.locator(".cm-lintRange-error")).toBeVisible(); // soulignée dans le texte
  await overlay.getByRole("button", { name: "Fermer" }).click();
  await expect(editor.locator(".cm-content")).toContainText("FORM t"); // la surcouche modifie le paramètre
});

test("éditeur SQL d'une base : tables rangées par schéma, colonnes et types à l'ouverture d'une table", async ({ page }) => {
  // base simulée : aucune base réelle n'est contactée par l'interface
  const json = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  const base = { name: "ma_base", kind: "postgres", host: "localhost", port: 5432, dbname: "ma_base", user: "lecteur", sslmode: "", options: {}, has_password: false, owner: "" };
  await page.route(/\/api\/connections$/, (route) => route.fulfill(json([base])));
  await page.route(/\/api\/connections\/ma_base\/schema$/, (route) =>
    route.fulfill(json({ dialect: "postgres", tables: { "public.communes": ["insee", "nom"], "public.routes": ["id"], "cadastre.parcelles": ["idu", "surface"] } })),
  );
  const types: Record<string, Record<string, string>> = {
    "public.communes": { insee: "character varying", nom: "text" },
    "public.routes": { id: "integer" },
    "cadastre.parcelles": { idu: "text", surface: "double precision" },
  };
  await page.route(/\/api\/connections\/ma_base\/columns\?/, (route) => {
    const table = new URL(route.request().url()).searchParams.get("table") ?? "";
    const columns = Object.entries(types[table] ?? {}).map(([name, type]) => ({ name, type }));
    return route.fulfill(json({ exists: true, case_sensitive: true, columns }));
  });
  await page.route(/\/api\/sql\/check$/, (route) => route.fulfill(json({ ok: true, error: null, line: null, column: null, skipped: null })));
  const flow = { name: "sql base", nodes: [{ id: "sql", type: "DatabaseQuery", position: [0, 0], params: { connection: "ma_base", sql: "SELECT * FROM " } }], links: [] };
  const created = await (await page.request.post("/api/flows", { data: { flow } })).json();
  await page.goto(`/#flow=${created.id}`);

  await page.getByTestId("node-sql").click();
  await page.getByRole("button", { name: "Éditeur SQL" }).first().click();
  const editor = page.getByRole("dialog", { name: /Éditeur SQL/ });
  const helper = editor.getByRole("complementary", { name: "Attributs et valeurs" });
  await expect(editor.locator(".cm-content")).toBeVisible();
  await expect(helper.getByRole("button", { name: "public", exact: true })).toBeVisible();
  await expect(helper.getByRole("button", { name: "parcelles", exact: true })).toHaveCount(0); // schémas repliés
  await helper.getByRole("button", { name: "Tables du schéma cadastre" }).click();
  await helper.getByRole("button", { name: "Colonnes de cadastre.parcelles" }).click();
  await expect(helper.locator(".helper-column", { hasText: "surface" })).toContainText("double precision");
  await editor.locator(".cm-content").press("End");
  await helper.getByRole("button", { name: "parcelles", exact: true }).click();
  await expect(editor.locator(".cm-content")).toHaveText("SELECT * FROM cadastre.parcelles");

  // recherche sur une colonne : son schéma et sa table se déplient
  await helper.getByRole("searchbox", { name: "Rechercher un attribut…" }).fill("insee");
  await expect(helper.getByRole("button", { name: "insee", exact: true })).toBeVisible();
  await expect(helper.getByRole("button", { name: "routes", exact: true })).toHaveCount(0);
  await page.request.delete(`/api/flows/${created.id}`);
});
