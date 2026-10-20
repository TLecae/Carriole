import { describe, expect, it } from "vitest";
import type { FlowMeta } from "./graph";
import { buildHints, resolvePreview, systemHints } from "./variables";

const meta = (over: Partial<FlowMeta>): FlowMeta => ({
  name: "t",
  description: "",
  publishedParams: [],
  variables: [],
  component: null,
  ...over,
});

describe("variables du flux", () => {
  it("résout les paramètres, les variables composées dans n'importe quel ordre et les sys: connus", () => {
    const hints = buildHints(
      meta({
        publishedParams: [{ name: "ROOT", label: "", type: "text", default: "Z:/data", required: false, choices: [] }],
        variables: [
          { name: "OUT", value: "${BASE}/sortie" },
          { name: "BASE", value: "${ROOT}/lot" },
        ],
      }),
      [],
    );
    const value = (token: string) => hints.find((h) => h.token === token)?.value;
    expect(value("BASE")).toBe("Z:/data/lot");
    expect(value("OUT")).toBe("Z:/data/lot/sortie");
    expect(value("sys:date")).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(value("sys:flow_dir")).toBeNull();
  });

  it("laisse ${…} tel quel quand la valeur n'est connue qu'au run", () => {
    const hints = [...systemHints(), { token: "A", group: "param" as const, value: "x" }];
    expect(resolvePreview("${A}/${sys:run_id}/${var:N}", hints)).toEqual({ text: "x/${sys:run_id}/${var:N}", complete: false });
    expect(resolvePreview("${A}", hints)).toEqual({ text: "x", complete: true });
  });
});
