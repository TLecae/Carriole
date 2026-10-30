import { describe, expect, it } from "vitest";
import type { JsonSchema } from "../api/types";
import { defaultValue, fieldMatches, labelOf, resolveSchema, textMatches, typeOf, widgetOf } from "./schema";

const defs: Record<string, JsonSchema> = {
  Rule: {
    type: "object",
    properties: {
      action: { enum: ["set", "rename", "remove"], default: "set", type: "string" },
      attribute: { type: "string", default: "", "x-etl-widget": "attribute" },
      count: { type: "integer", default: 3 },
    },
  },
};

describe("schéma de paramètres", () => {
  it("suit $ref et les anyOf nullables", () => {
    expect(resolveSchema({ $ref: "#/$defs/Rule" }, defs).properties?.action).toBeDefined();
    const nullable = resolveSchema({ anyOf: [{ type: "string" }, { type: "null" }], title: "Couche" }, defs);
    expect(typeOf(nullable)).toBe("string");
    expect(nullable.title).toBe("Couche");
  });

  it("calcule les valeurs par défaut, y compris imbriquées", () => {
    expect(defaultValue({ $ref: "#/$defs/Rule" }, defs)).toEqual({ action: "set", attribute: "", count: 3 });
    expect(defaultValue({ type: "array" }, defs)).toEqual([]);
    expect(defaultValue({ type: "boolean" }, defs)).toBe(false);
    expect(defaultValue({ type: "string", enum: ["a", "b"] }, defs)).toBe("a");
  });

  it("donne un libellé lisible et lit le widget", () => {
    expect(labelOf("new_name", {})).toBe("New name");
    expect(labelOf("x", { title: "Nouveau nom" })).toBe("Nouveau nom");
    expect(widgetOf({ "x-etl-widget": "crs" })).toBe("crs");
  });
});

describe("recherche d'un paramètre", () => {
  const defs = {};
  const functions: JsonSchema = { title: "Statistiques", type: "array", items: { enum: ["count", "sum", "median"] }, description: "Calculées pour chaque attribut." };

  it("ignore la casse et les accents, et demande tous les mots", () => {
    expect(textMatches("evaluee", "Expression évaluée")).toBe(true);
    expect(textMatches("EXPRESSION éval", "Expression évaluée")).toBe(true);
    expect(textMatches("expression date", "Expression évaluée")).toBe(false);
    expect(textMatches("  ", "n'importe quoi")).toBe(true);
  });

  it("cherche dans le libellé, le nom, la description et les valeurs proposées", () => {
    expect(fieldMatches("functions", functions, defs, "statist")).toBe(true);
    expect(fieldMatches("functions", functions, defs, "function")).toBe(true);
    expect(fieldMatches("functions", functions, defs, "chaque attribut")).toBe(true);
    expect(fieldMatches("functions", functions, defs, "médiane")).toBe(false);
    expect(fieldMatches("functions", functions, defs, "median")).toBe(true);
  });
});
