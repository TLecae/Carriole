import { describe, expect, it } from "vitest";
import { MAX_SCROLL_HEIGHT, ScrollScale } from "./scrollScale";

const ROW = 26;
const scale = (rows: number, view = 400) => Object.assign(new ScrollScale(), { total: rows * ROW, view });

describe("ScrollScale", () => {
  it("sous le plafond, l'ascenseur est la position dans la table", () => {
    const s = scale(100_000);
    expect(s.height).toBe(100_000 * ROW);
    expect(s.fromScroll(123_456)).toBe(123_456);
    expect(s.toScroll(50_000)).toBe(50_000);
    expect(s.offset).toBe(0);
  });

  it("au-delà, la fin de la table reste accessible et une ligne lointaine se retrouve", () => {
    const s = scale(13_265_825);
    const room = MAX_SCROLL_HEIGHT - s.view;
    expect(s.height).toBe(MAX_SCROLL_HEIGHT);
    expect(s.fromScroll(room)).toBe(s.total - s.view); // ascenseur en bas : dernière ligne
    expect(s.fromScroll(0)).toBe(0);

    const target = 12_000_000 * ROW; // ligne cliquée sur la carte
    const top = s.toScroll(target);
    expect(top).toBeGreaterThan(0);
    expect(top).toBeLessThan(room);
    expect(s.fromScroll(top)).toBe(target); // l'événement de défilement qui suit ne la déplace pas
  });

  it("un pas de molette avance ligne à ligne, un saut suit l'ascenseur en proportion", () => {
    const s = scale(13_265_825);
    const room = MAX_SCROLL_HEIGHT - s.view;
    const middle = s.fromScroll(room / 2);
    expect(middle / (s.total - s.view)).toBeCloseTo(0.5, 3);
    expect(s.fromScroll(room / 2 + 100)).toBe(middle + 100);
    expect(s.fromScroll(room / 2 + 100 - 3 * ROW)).toBe(middle + 100 - 3 * ROW);
  });

  it("une position proche se rejoint sans saut", () => {
    const s = scale(13_265_825);
    const at = s.fromScroll(MAX_SCROLL_HEIGHT / 4);
    const top = s.toScroll(at + 10 * ROW);
    expect(top).toBe(MAX_SCROLL_HEIGHT / 4 + 10 * ROW);
    expect(s.fromScroll(top)).toBe(at + 10 * ROW);
  });
});
