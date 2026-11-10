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
