import { describe, expect, it } from "vitest";
import { formatSize, join, startFolder } from "./files";

describe("startFolder", () => {
  it("ouvre l'explorateur dans le dossier du fichier saisi", () => {
    expect(startFolder("/data/sources/communes.gpkg")).toEqual({ dir: "/data/sources", name: "communes.gpkg" });
  });
  it("ignore le motif : le dossier est celui qui précède le premier joker", () => {
    expect(startFolder("/data/sources/2025/**/*.gpkg")).toEqual({ dir: "/data/sources/2025", name: "" });
  });
  it("champ vide ou sans dossier : l'explorateur s'ouvre sur la liste des emplacements", () => {
    expect(startFolder("")).toEqual({ dir: "", name: "" });
    expect(startFolder("communes.gpkg")).toEqual({ dir: "", name: "" });
    expect(startFolder("${FICHIER}")).toEqual({ dir: "", name: "" });
  });
});

describe("join", () => {
  it("garde le séparateur du serveur", () => {
    expect(join("/data/out", "a.csv")).toBe("/data/out/a.csv");
    expect(join("/data/out/", "a.csv")).toBe("/data/out/a.csv");
    expect(join("C:\\data\\out", "a.csv")).toBe("C:\\data\\out\\a.csv");
  });
});

describe("formatSize", () => {
  it("choisit l'unité lisible", () => {
    expect(formatSize(null)).toBe("");
    expect(formatSize(512)).toBe("512 o");
    expect(formatSize(1536)).toBe("1,5 Ko");
    expect(formatSize(5 * 1024 * 1024)).toBe("5 Mo");
    expect(formatSize(3 * 1024 ** 3)).toBe("3 Go");
  });
});
