import { describe, expect, it } from "vitest";
import type { NodeResult } from "../api/types";
import { rollupComponents } from "./rollup";

const r = (status: NodeResult["status"], seconds?: number, error?: string): NodeResult => ({ status, seconds, error: error ?? null }) as NodeResult;

describe("rollupComponents", () => {
  it("laisse intacts les flux sans composant", () => {
    const nodes = { a: r("done"), b: r("running") };
    expect(rollupComponents(nodes)).toBe(nodes);
  });

  it("résume les nœuds internes sous le nœud composant", () => {
    const out = rollupComponents({ read: r("done", 1), f__t: r("done", 0.5), f__s: r("done", 0.25) });
    expect(out.f.status).toBe("done");
    expect(out.f.seconds).toBe(0.75);
    expect(out.read.status).toBe("done");
  });

  it("l'état le plus grave l'emporte : erreur, annulé, en cours, en attente", () => {
    expect(rollupComponents({ f__a: r("done"), f__b: r("error", 1, "boum") }).f).toMatchObject({ status: "error", error: "boum" });
    expect(rollupComponents({ f__a: r("cancelled"), f__b: r("running") }).f.status).toBe("cancelled");
    expect(rollupComponents({ f__a: r("done"), f__b: r("running") }).f.status).toBe("running");
    expect(rollupComponents({ f__a: r("done"), f__b: r("pending") }).f.status).toBe("pending");
  });

  it("se met à jour quand les nœuds internes avancent (l'entrée fabriquée n'est pas figée)", () => {
    const first = rollupComponents({ f__a: r("pending"), f__b: r("pending") });
    expect(first.f.status).toBe("pending");
    const second = rollupComponents({ ...first, f__a: r("done", 1), f__b: r("running") });
    expect(second.f.status).toBe("running");
    const third = rollupComponents({ ...second, f__a: r("done", 1), f__b: r("done", 2) });
    expect(third.f).toMatchObject({ status: "done", seconds: 3 });
  });

  it("cache quand tout vient du cache ; ignore un vrai nœud dont le nom contient « __ »", () => {
    expect(rollupComponents({ f__a: r("cached"), f__b: r("cached") }).f.status).toBe("cached");
    const real = { f: r("done"), f__x: r("done") };
    expect(rollupComponents(real)).toBe(real);
  });
});
