/**
 * « Gérer des attributs » (RefactorAttributes) vu comme un tableau : une ligne par attribut reçu, plus les attributs
 * créés. Le moteur, lui, applique une liste de règles dans l'ordre ; ce module passe de l'un à l'autre.
 */

export interface Rule {
  action: "set" | "rename" | "remove";
  attribute: string;
  new_name: string;
  expression: string;
}

/** `input` : attribut reçu (null : attribut créé) ; `output` : son nom en sortie ; `expression` : nouvelle valeur. */
export interface Row {
  input: string | null;
  output: string;
  expression: string;
  removed: boolean;
}

/** Une règle avec tous ses champs (ceux que son action n'utilise pas, vides) : deux listes se comparent ainsi. */
export const norm = (r: Partial<Rule>): Rule => ({
  action: r.action ?? "set",
  attribute: r.attribute ?? "",
  new_name: r.action === "rename" ? (r.new_name ?? "") : "",
  expression: (r.action ?? "set") === "set" ? (r.expression ?? "") : "",
});

const set = (attribute: string, expression: string): Rule => ({ action: "set", attribute, new_name: "", expression });

/**
 * Règles produites par le tableau. Toutes les valeurs se calculent d'abord (sur les attributs reçus), puis viennent
 * les suppressions et les renommages ; une valeur dont le nom de sortie libère celui d'un attribut reçu (renommé ou
 * supprimé) se calcule en dernier.
 */
export function rulesFromRows(rows: Row[]): Rule[] {
  const received = new Set(rows.flatMap((r) => (r.input === null ? [] : [r.input])));
  const first: Rule[] = [];
  const removes: Rule[] = [];
  const renames: Rule[] = [];
  const last: Rule[] = [];
  const value = (name: string, expression: string) => (received.has(name) ? last : first).push(set(name, expression));
  for (const row of rows) {
    if (row.input === null) {
      value(row.output, row.expression);
    } else if (row.removed) {
      removes.push({ action: "remove", attribute: row.input, new_name: "", expression: "" });
    } else if (row.expression && row.output === row.input) {
      first.push(set(row.input, row.expression)); // valeur remplacée sur place
    } else if (row.expression) {
      value(row.output, row.expression); // renommé avec une nouvelle valeur : créé, l'ancien supprimé
      removes.push({ action: "remove", attribute: row.input, new_name: "", expression: "" });
    } else if (row.output !== row.input) {
      renames.push({ action: "rename", attribute: row.input, new_name: row.output, expression: "" });
    }
  }
  // un renommage vers un nom encore pris attend que ce nom soit renommé à son tour
  const ordered: Rule[] = [];
  let pending = renames;
  while (pending.length) {
    const ready = pending.filter((r) => !pending.some((o) => o !== r && o.attribute === r.new_name));
    if (!ready.length) {
      ordered.push(...pending); // cycle (échange de noms) : le moteur le signalera
      break;
    }
    ordered.push(...ready);
    pending = pending.filter((r) => !ready.includes(r));
  }
  return [...first, ...removes, ...ordered, ...last];
}

/**
 * Lignes du tableau pour ces règles ; null si le tableau ne les reproduit pas à l'identique (règles écrites une à une,
 * dans un ordre qui compte) : le formulaire garde alors la liste des règles.
 */
export function rowsFromRules(rules: Partial<Rule>[], received: string[]): Row[] | null {
  const rows: Row[] = received.map((name) => ({ input: name, output: name, expression: "", removed: false }));
  const byInput = new Map(rows.map((r) => [r.input as string, r]));
  const wanted = rules.map(norm);
  for (const r of wanted) {
    if (r.action === "set") {
      const row = byInput.get(r.attribute);
      if (row && row.output === r.attribute && !row.removed && !row.expression) row.expression = r.expression;
      else rows.push({ input: null, output: r.attribute, expression: r.expression, removed: false });
      continue;
    }
    let row = byInput.get(r.attribute);
    if (!row) {
      row = { input: r.attribute, output: r.attribute, expression: "", removed: false }; // absent de l'entrée
      rows.push(row);
      byInput.set(r.attribute, row);
    }
    if (r.action === "remove") row.removed = true;
    else row.output = r.new_name;
  }
  const back = rulesFromRows(rows);
  return JSON.stringify(back) === JSON.stringify(wanted) ? rows : null;
}

/** Ce que fait une ligne, pour la colonne « Action ». */
export function actionOf(row: Row): "create" | "remove" | "rename" | "rename-set" | "set" | "none" {
  if (row.input === null) return "create";
  if (row.removed) return "remove";
  if (row.expression) return row.output === row.input ? "set" : "rename-set";
  return row.output === row.input ? "none" : "rename";
}
