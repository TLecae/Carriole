import { expect, test } from "@playwright/test";

// E2E_MODE=server : `etl serve --server`, comptes du fichier créé par playwright.config.ts (ADR 0052).

test("connexion : l'accueil n'apparaît qu'après le mot de passe, la déconnexion y ramène", async ({ page }) => {
  await page.goto("/");
  const form = page.getByTestId("login-form");
  await expect(form).toBeVisible();
  await page.getByLabel("Identifiant").fill("alice");
  await page.getByLabel("Mot de passe").fill("mauvais");
  await page.getByRole("button", { name: "Se connecter" }).click();
  await expect(form.getByRole("alert")).toContainText("identifiant ou mot de passe incorrect");

  await page.getByLabel("Mot de passe").fill("alice-e2e-mdp");
  await page.getByRole("button", { name: "Se connecter" }).click();
  await expect(page.getByRole("heading", { name: "Mes flux" })).toBeVisible();
  await page.getByRole("button", { name: "Alice Martin" }).click();
  await expect(page.getByTestId("user-identity")).toContainText("alice");
  await expect(page.getByTestId("user-identity")).not.toContainText("administrateur");
  await page.getByRole("menuitem", { name: "Se déconnecter" }).click();
  await expect(form).toBeVisible();
});

test("API : sans session, rien ; un en-tête d'identité posé par le client n'ouvre rien", async ({ request }) => {
  expect((await request.get("/api/health")).status()).toBe(200);
  expect((await request.get("/api/flows")).status()).toBe(401);
  expect((await request.get("/api/flows", { headers: { "X-ETL-User": "admin" } })).status()).toBe(401);
  const login = await request.post("/api/auth/login", { data: { username: "admin", password: "admin-e2e-mdp" } });
  expect(login.ok()).toBeTruthy();
  expect((await login.json()).admin).toBe(true);
  expect((await request.get("/api/flows")).status()).toBe(200);
});

async function signOut(page: import("@playwright/test").Page, display: string) {
  await page.getByRole("button", { name: display }).click();
  await page.getByRole("menuitem", { name: "Se déconnecter" }).click();
  await expect(page.getByTestId("login-form")).toBeVisible(); // la page s'est rechargée
}

async function signIn(page: import("@playwright/test").Page, name: string, password: string) {
  await page.goto("/");
  await page.getByLabel("Identifiant").fill(name);
  await page.getByLabel("Mot de passe").fill(password);
  await page.getByRole("button", { name: "Se connecter" }).click();
  await expect(page.getByRole("heading", { name: "Mes flux" })).toBeVisible();
}

test("réglages : un utilisateur les consulte, un administrateur les modifie et voit le serveur", async ({ page }) => {
  await signIn(page, "alice", "alice-e2e-mdp");
  await page.getByRole("button", { name: "Paramètres" }).click();
  await expect(page.getByLabel("Utilisateur")).toHaveValue("alice");
  await page.getByRole("tab", { name: "Calcul" }).click();
  await expect(page.getByTestId("settings-readonly")).toBeVisible();
  await expect(page.locator("fieldset.settings-fieldset input").first()).toBeDisabled();
  await page.getByRole("tab", { name: "Serveur" }).click();
  await expect(page.getByTestId("server-settings")).toContainText("fichier de comptes");
  await expect(page.getByTestId("server-settings")).not.toContainText("Administrateurs");
  await page.getByRole("button", { name: "Fermer" }).click();
  await signOut(page, "Alice Martin");

  await signIn(page, "admin", "admin-e2e-mdp");
  await page.getByRole("button", { name: "Paramètres" }).click();
  await page.getByRole("tab", { name: "Calcul" }).click();
  await expect(page.getByTestId("settings-readonly")).toHaveCount(0);
  await expect(page.locator("fieldset.settings-fieldset input").first()).toBeEnabled();
  await page.getByLabel("Runs simultanés par utilisateur").fill("3");
  await page.getByLabel("Mémoire par utilisateur (Mo)").focus(); // la sortie du champ enregistre
  await expect(page.getByText("Ce réglage s'applique au prochain démarrage de l'application.")).toBeVisible();
  await page.getByRole("tab", { name: "Serveur" }).click();
  await expect(page.getByTestId("server-settings")).toContainText("group:carriole-admins");
});

test("partage : un flux partagé en lecture s'ouvre sans être modifié, Ctrl+S en fait une copie", async ({ page }) => {
  await signIn(page, "alice", "alice-e2e-mdp");
  const name = `Partage ${Date.now()}`;
  const created = await page.request.post("/api/flows", { data: { flow: { name, nodes: [], links: [] } } });
  expect(created.ok()).toBeTruthy();
  await page.reload();
  await page.getByRole("button", { name: `Partager ${name}` }).click();
  await page.getByLabel("Partager avec").selectOption("group");
  await page.getByLabel("Nom", { exact: true }).fill("equipe");
  await page.getByRole("button", { name: "Ajouter" }).click();
  await expect(page.getByTestId("share-list")).toContainText("Groupe equipe");
  await page.getByRole("button", { name: "Enregistrer" }).click();
  await signOut(page, "Alice Martin");

  await signIn(page, "carol", "carol-e2e-mdp");
  const card = page.getByTestId("flow-card").filter({ hasText: name });
  await expect(card).toContainText("lecture seule");
  await expect(card.getByRole("button", { name: `Partager ${name}` })).toHaveCount(0);
  await card.getByRole("button", { name: `Ouvrir ${name}` }).click();
  await expect(page.getByTestId("flow-readonly")).toBeVisible();
  await page.keyboard.press("Control+s");
  await expect(page.getByText("Copie enregistrée à votre nom")).toBeVisible();
  await expect(page.getByTestId("flow-readonly")).toHaveCount(0);
});

test("journal d'activité : l'administrateur voit les connexions et les écritures, pas les autres", async ({ page }) => {
  await signIn(page, "alice", "alice-e2e-mdp");
  await page.getByRole("button", { name: "Outils" }).click();
  await expect(page.getByRole("menuitem", { name: "Journal d'activité" })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await signOut(page, "Alice Martin");

  await signIn(page, "admin", "admin-e2e-mdp");
  await page.getByRole("button", { name: "Outils" }).click();
  await page.getByRole("menuitem", { name: "Journal d'activité" }).click();
  const table = page.getByTestId("audit-table");
  await expect(table).toContainText("Connexion refusée"); // le mauvais mot de passe du premier scénario
  await page.getByLabel("Utilisateur", { exact: true }).fill("alice");
  await expect(table).toContainText("Déconnexion");
  await expect(table).not.toContainText("admin");
});
