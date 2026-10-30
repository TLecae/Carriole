import { describe, expect, it } from "vitest";
import { CATEGORY_COLORS, NO_VALUE, RAMP, categoriesColoring, classesColoring, rampColors, shapeOf } from "./legend";

describe("classes d'un attribut numérique", () => {
  it("une couleur par classe, de la rampe, et un palier par borne intérieure", () => {
    const c = classesColoring([0, 10, 20, 30, 40, 50], 0)!;
    expect(c.items.map((i) => i.color)).toEqual(RAMP);
    expect(c.items[0].label).toBe("0 – 10");
    expect(c.expression).toEqual(["case", ["has", "c"], ["step", ["to-number", ["get", "c"]], RAMP[0], 10, RAMP[1], 20, RAMP[2], 30, RAMP[3], 40, RAMP[4]], NO_VALUE]);
  });

  it("peu de valeurs distinctes : moins de classes, couleurs étalées sur la rampe ; sans valeur en gris", () => {
    const c = classesColoring([1, 2, 3], 4)!;
    expect(c.items.map((i) => i.color)).toEqual([RAMP[0], RAMP[4], NO_VALUE]);
    expect(c.items.at(-1)!.label).toBe("Sans valeur");
    expect(rampColors(3)).toEqual([RAMP[0], RAMP[2], RAMP[4]]);
  });

  it("une seule valeur, ou aucune", () => {
    expect(classesColoring([7], 0)!.expression).toEqual(["case", ["has", "c"], RAMP[2], NO_VALUE]);
    expect(classesColoring([], 3)).toBeNull();
  });

  it("des décimales tant qu'il en faut pour distinguer les bornes", () => {
    expect(classesColoring([0.12, 0.5, 0.91], 0)!.items[0].label).toMatch(/^0[.,]12 – 0[.,]5$/);
  });
});

describe("catégories", () => {
  it("les valeurs les plus fréquentes en couleur, le reste et les vides en gris", () => {
    const values = [{ value: "31", count: 9 }, { value: null, count: 3 }, { value: "09", count: 2 }];
    const c = categoriesColoring(values)!;
    expect(c.items).toEqual([
      { label: "31", color: CATEGORY_COLORS[0] },
      { label: "09", color: CATEGORY_COLORS[1] },
      { label: "Sans valeur", color: NO_VALUE },
    ]);
    expect(c.expression).toEqual(["match", ["to-string", ["get", "c"]], "31", CATEGORY_COLORS[0], "09", CATEGORY_COLORS[1], NO_VALUE]);
  });

  it("plus de valeurs que de couleurs : « Autres valeurs »", () => {
    const values = Array.from({ length: CATEGORY_COLORS.length + 1 }, (_, i) => ({ value: `v${i}`, count: 1 }));
    const c = categoriesColoring(values)!;
    expect(c.items).toHaveLength(CATEGORY_COLORS.length + 1);
    expect(c.items.at(-1)).toEqual({ label: "Autres valeurs", color: NO_VALUE });
  });

  it("rien à colorer", () => {
    expect(categoriesColoring([{ value: null, count: 5 }])).toBeNull();
  });
});

it("forme du symbole selon les géométries", () => {
  expect(shapeOf(["POINT"])).toBe("point");
  expect(shapeOf(["LINESTRING", "MULTILINESTRING"])).toBe("line");
  expect(shapeOf(["POINT", "POLYGON"])).toBe("polygon");
});
