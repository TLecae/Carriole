import type { Locator, Page } from "@playwright/test";

/** Ouvre un menu de la barre du haut (Fichier, Édition, Exécution, Outils) et choisit une entrée. */
export async function chooseMenu(page: Page, menu: string, item: string): Promise<void> {
  await page.getByRole("button", { name: menu, exact: true }).click();
  await page.getByRole("menuitem", { name: item, exact: true }).click();
}

/** Un modificateur de la palette, sa catégorie dépliée (elles sont repliées à l'ouverture). */
export async function inPalette(page: Page, name: string): Promise<Locator> {
  const item = page.getByTestId(`palette-${name}`);
  await item.evaluate((el) => {
    const group = el.closest("details");
    if (group) group.open = true;
  });
  return item;
}

/** Ajoute un modificateur au centre du canevas (double-clic dans la palette). */
export async function addFromPalette(page: Page, name: string): Promise<void> {
  await (await inPalette(page, name)).dblclick();
}
