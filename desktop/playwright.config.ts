import { defineConfig } from "@playwright/test";

// Test de fumée de la vraie application Electron (fenêtre, dialogues natifs, backend détaché).
export default defineConfig({
  testDir: "./tests",
  timeout: 120_000,
  expect: { timeout: 30_000 },
  workers: 1,
  reporter: [["list"]],
});
