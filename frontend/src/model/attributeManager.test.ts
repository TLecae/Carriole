import { describe, expect, it } from "vitest";
import { actionOf, rowsFromRules, rulesFromRows, type Row } from "./attributeManager";

const row = (input: string | null, output: string, expression = "", removed = false): Row => ({ input, output, expression, removed });

describe("tableau de « Gérer des attributs »", () => {
  it("montre chaque attribut reçu, même sans règle", () => {
    expect(rowsFromRules([], ["id", "nom"])).toEqual([row("id", "id"), row("nom", "nom")]);
    expect(rulesFromRows([row("id", "id"), row("nom", "nom")])).toEqual([]);
  });

  it("calcule les valeurs sur les attributs reçus, avant suppressions et renommages", () => {
    const rules = rulesFromRows([row("id", "ident"), row("nom", "nom", "upper(nom)"), row("old", "old", "", true), row(null, "surf_ha", '"surface" / 10000')]);
    expect(rules.map((r) => [r.action, r.attribute, r.new_name])).toEqual([
      ["set", "nom", ""],
      ["set", "surf_ha", ""],
      ["remove", "old", ""],
      ["rename", "id", "ident"],
    ]);
    expect(rowsFromRules(rules, ["id", "nom", "old"])).toEqual([row("id", "ident"), row("nom", "nom", "upper(nom)"), row("old", "old", "", true), row(null, "surf_ha", '"surface" / 10000')]);
  });

  it("un renommage vers un nom libéré attend que ce nom soit renommé", () => {
    const rules = rulesFromRows([row("a", "b"), row("b", "c")]);
    expect(rules.map((r) => `${r.attribute}>${r.new_name}`)).toEqual(["b>c", "a>b"]);
  });

  it("une valeur qui reprend le nom d'un attribut renommé se calcule en dernier", () => {
    const rules = rulesFromRows([row("code", "code_old"), row(null, "code", "'x'")]);
    expect(rules.map((r) => r.action)).toEqual(["rename", "set"]);
  });

  it("garde la liste des règles quand le tableau ne la reproduit pas", () => {
    // ordre qui compte : renommer puis calculer sur le nouveau nom
    const rules = [
      { action: "rename" as const, attribute: "a", new_name: "b" },
      { action: "set" as const, attribute: "b", expression: "1" },
    ];
    expect(rowsFromRules(rules, ["a"])).toBeNull();
  });

  it("signale l'action de chaque ligne", () => {
    expect([row("a", "a"), row("a", "b"), row("a", "a", "1"), row("a", "b", "1"), row("a", "a", "", true), row(null, "n", "1")].map(actionOf)).toEqual([
      "none",
      "rename",
      "set",
      "rename-set",
      "remove",
      "create",
    ]);
  });
});
