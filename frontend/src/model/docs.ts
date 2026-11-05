import { language } from "../i18n";

// catalogue des modificateurs publié avec le code (docs/catalogue.md, généré par `etl modifiers doc`)
const DOCS = "https://github.com/TLecae/Carriole/blob/main/docs/";

/** Page du catalogue sur un modificateur, ou sur l'un de ses paramètres (ancres de `catalogue.py`). */
export function catalogueUrl(modifier: string, param?: string): string {
  const page = language() === "fr" ? "catalogue.md" : "en/catalogue.md";
  return `${DOCS}${page}#${modifier.toLowerCase()}${param ? `-${param.toLowerCase()}` : ""}`;
}
