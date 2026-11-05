import { describe, expect, it } from "vitest";
import { bySchema, completionSchema, containsWhere, literal, quoteName, tableName } from "./fieldHelper";

describe("aide à la saisie des éditeurs", () => {
  it("écrit les noms et les valeurs comme SQL les attend", () => {
    expect(quoteName('nom "court"')).toBe('"nom ""court"""');
    expect(tableName("public.communes")).toBe("public.communes");
    expect(tableName("Carto.Communes 2024")).toBe('"Carto"."Communes 2024"');
    expect(literal("L'Isle-Jourdain")).toBe("'L''Isle-Jourdain'");
    expect([literal(12.5), literal(true), literal(null)]).toEqual(["12.5", "TRUE", "NULL"]);
    expect(containsWhere("nom", "it'")).toBe(`CAST("nom" AS VARCHAR) ILIKE '%it''%'`);
    expect(containsWhere("nom", "")).toBe("");
  });

  it("range les tables d'une base par schéma, et filtre sur le schéma, la table ou une colonne", () => {
    const tables = { "public.communes": ["insee", "nom"], "public.routes": ["id"], "cadastre.parcelles": ["idu"], seule: [] };
    expect(bySchema(tables).map((g) => [g.schema, g.tables.map((x) => x.short)])).toEqual([
      ["public", ["communes", "routes"]],
      ["cadastre", ["parcelles"]],
      ["", ["seule"]],
    ]);
    expect(bySchema(tables, "INSEE").map((g) => g.tables.map((x) => x.name))).toEqual([["public.communes"]]);
    expect(bySchema(tables, "cadas")[0].tables[0]).toEqual({ name: "cadastre.parcelles", short: "parcelles", columns: ["idu"] });
  });
  it("propose les colonnes d'une seule entrée sans préfixe, sinon par nom de port", () => {
    const one = { kind: "inputs" as const, inputs: [{ port: "input", label: "", fields: [{ name: "a", type: "int64" }], ref: null }] };
    expect(completionSchema(one)).toEqual({ data: ["a"] });
    const two = { kind: "inputs" as const, inputs: [one.inputs[0], { ...one.inputs[0], port: "join", fields: [{ name: "b", type: "string" }] }] };
    expect(completionSchema(two)).toEqual({ input: ["a"], join: ["b"] });
  });
});
