import { describe, expect, it } from "vitest";
import { comboMatches } from "./Combo";

describe("comboMatches", () => {
  const tables = ["acteur", "commune_za", "zone_activite", "Zone_Extension"];

  it("propose tout tant que rien n'est tapé, ou quand la valeur est déjà une proposition", () => {
    expect(comboMatches(tables, "", true)).toEqual(tables);
    expect(comboMatches(tables, "zone_activite", false)).toEqual(tables);
    expect(comboMatches(tables, "zone_activite", true)).toEqual(tables);
  });

  it("garde celles qui contiennent la saisie, sans tenir compte de la casse", () => {
    expect(comboMatches(tables, "zone", true)).toEqual(["zone_activite", "Zone_Extension"]);
    expect(comboMatches(tables, "ZA", true)).toEqual(["commune_za"]);
    expect(comboMatches(tables, "nouvelle", true)).toEqual([]);
  });
});
