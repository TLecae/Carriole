import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const dataInfo = vi.fn();
vi.mock("../api/client", async (importActual) => {
  const actual = await importActual<typeof import("../api/client")>();
  return { ...actual, api: { ...actual.api, dataInfo: (...args: unknown[]) => dataInfo(...args) } };
});

const { ApiError } = await import("../api/client");
const { useInspector } = await import("./inspector");

const preparing = () => Promise.reject(new ApiError(425, "préparation des données…"));

describe("inspecteur : port en préparation puis nœud sans données", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    dataInfo.mockReset();
    dataInfo.mockImplementation(preparing);
  });
  afterEach(() => {
    vi.useRealTimers();
    void useInspector.getState().setTarget(null);
  });

  // « stats » se prépare (calculé à la demande), puis on choisit « buffer », en cours de calcul (pas encore de données)
  const run = { id: "r1", nodes: { stats: { rows: { output: 10 } }, buffer: { rows: {} } } };

  it("un seul message : la préparation abandonnée n'est plus affichée ni redemandée", async () => {
    useInspector.getState().showNode(run, "stats");
    await vi.advanceTimersByTimeAsync(1200);
    expect(useInspector.getState().preparing).toBe(true);
    const asked = dataInfo.mock.calls.length;

    useInspector.getState().showNode(run, "buffer");
    const s = useInspector.getState();
    expect(s.emptyNode).toBe("buffer");
    expect(s.target).toBeNull();
    expect(s.loading).toBe(false);
    expect(s.preparing).toBe(false);

    await vi.advanceTimersByTimeAsync(3000);
    expect(dataInfo.mock.calls.length).toBeLessThanOrEqual(asked + 1); // la demande en vol au plus, puis plus rien
  });

  it("rechoisir le nœud abandonné relance sa préparation", async () => {
    useInspector.getState().showNode(run, "stats");
    await vi.advanceTimersByTimeAsync(600);
    useInspector.getState().showNode(run, "buffer");
    useInspector.getState().showNode(run, "stats");
    expect(useInspector.getState().target?.node).toBe("stats");
    await vi.advanceTimersByTimeAsync(600);
    expect(useInspector.getState().preparing).toBe(true);
  });
});
