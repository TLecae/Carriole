import type { ElectronApplication, Page } from "@playwright/test";

/** La fenêtre de Carriole (l'interface servie par le moteur), pas la petite fenêtre d'ouverture (page data:). */
export async function mainWindow(app: ElectronApplication, timeoutMs = 120_000): Promise<Page> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const found = app.windows().find((w) => w.url().startsWith("http"));
    if (found) return found;
    if (Date.now() > deadline) throw new Error("la fenêtre de Carriole ne s'est pas ouverte");
    await app.waitForEvent("window", { timeout: 500 }).catch(() => undefined);
  }
}
