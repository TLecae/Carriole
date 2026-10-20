import { describe, expect, it } from "vitest";
import type { JsonSchema } from "../api/types";
import { defaultValue, labelOf, resolveSchema, typeOf, widgetOf } from "./schema";

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
