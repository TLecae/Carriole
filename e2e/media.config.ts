import { defineConfig } from "@playwright/test";
import path from "node:path";
import base from "./playwright.config";

// Captures et vidéo du site et du README (scripts/site_media.ps1) : le vrai backend de playwright.config.ts, le scénario
// de media/ ; MEDIA_LANG : langue de l'interface (en, fr), MEDIA_OUT : dossier des fichiers produits.
const out = process.env.MEDIA_OUT ?? path.resolve("media-out");
const use = {
  ...base.use,
  // jeton en cookie (voir media/demo.media.ts), pas en en-tête : ajouté à toutes les requêtes, il ferait refuser les
  // tuiles du fond de carte (CORS)
  extraHTTPHeaders: {},
  colorScheme: "light" as const,
  trace: "off" as const,
};

export default defineConfig({
  ...base,
  testDir: "./media",
  testMatch: /.*\.media\.ts/,
  testIgnore: [],
  timeout: 300_000,
  outputDir: path.join(out, "raw"),
  projects: [
    {
      name: "video",
      grep: /vidéo :/,
      use: { ...use, viewport: { width: 1280, height: 800 }, video: { mode: "on", size: { width: 1280, height: 800 } } },
    },
    { name: "captures", grep: /captures :/, use: { ...use, viewport: { width: 1500, height: 900 }, video: "off" } },
  ],
});
