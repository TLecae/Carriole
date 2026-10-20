import { afterEach, describe, expect, it } from "vitest";
import en from "./locales/en.json";
import { availableLanguages, formatCount, formatNumber, language, setLanguage, t } from "./i18n";

const sources = import.meta.glob<string>(["./**/*.ts", "./**/*.tsx", "!./**/*.test.ts", "!./**/*.test.tsx"], {
  query: "?raw",
  import: "default",
  eager: true,
});

/** Les textes passés à `t("…")` dans le code : ce sont les clés du catalogue. */
function usedKeys(): Map<string, string> {
  const found = new Map<string, string>();
  const call = /(?<![\w.])t\(\s*("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')/g;
  for (const [file, code] of Object.entries(sources)) {
    for (const match of code.matchAll(call)) {
      const literal = match[1];
      const text = literal.startsWith('"') ? (JSON.parse(literal) as string) : literal.slice(1, -1).replace(/\\'/g, "'").replace(/\\\\/g, "\\");
      found.set(text, file);
    }
  }
  return found;
}

describe("catalogue anglais", () => {
  it("traduit tous les textes du code", () => {
    const missing = [...usedKeys().entries()].filter(([text]) => !(text in en)).map(([text, file]) => `${file} : ${text}`);
    expect(missing).toEqual([]);
  });

  it("ne garde pas de traduction devenue inutile", () => {
    const used = usedKeys();
    expect(Object.keys(en).filter((key) => !used.has(key))).toEqual([]);
  });

  it("garde les mêmes {{valeurs}} que le français", () => {
    const names = (s: string) => [...s.matchAll(/\{\{(\w+)\}\}/g)].map((m) => m[1]).sort();
    for (const [fr, translated] of Object.entries(en)) expect(names(translated), fr).toEqual(names(fr));
  });
});

describe("aucun texte français ne reste en dur dans l'interface", () => {
  it("ni dans le JSX ni dans les attributs", () => {
    const accented = /[àâçéèêëîïôûùüœÀÉÈÊ]/;
    const hits: string[] = [];
    for (const [file, code] of Object.entries(sources)) {
      if (file.endsWith("i18n.ts")) continue;
      // un texte accentué est admis seulement dans un appel t("…") ; tout le reste est un texte oublié
      const withoutCalls = code.replace(/(?<![\w.])t\(\s*(?:"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')/g, "t(");
      withoutCalls.split(/\r?\n/).forEach((line, i) => {
        const trimmed = line.trim();
        if (/^(\/\/|\/\*|\*)/.test(trimmed)) return; // commentaires
        const withoutComment = line.replace(/\s\/\/ .*$/, "").replace(/\/\*.*?\*\//g, "");
        if (accented.test(withoutComment)) hits.push(`${file}:${i + 1}: ${trimmed.slice(0, 100)}`);
      });
    }
    expect(hits).toEqual([]);
  });
});

describe("t()", () => {
  afterEach(async () => setLanguage("fr"));

  it("rend le texte français tel quel, valeurs insérées", async () => {
    expect(language()).toBe("fr");
    expect(t("Enregistrer")).toBe("Enregistrer");
    expect(t("{{count}} nœud(s).", { count: 3 })).toBe("3 nœud(s).");
  });

  it("traduit en anglais et retombe sur le français quand la clé manque", async () => {
    await setLanguage("en");
    expect(language()).toBe("en");
    expect(t("Enregistrer")).toBe("Save");
    expect(t("{{count}} nœud(s).", { count: 3 })).toBe("3 node(s).");
    expect(t("Texte jamais traduit")).toBe("Texte jamais traduit");
  });

  it("ignore une langue sans catalogue", async () => {
    await setLanguage("klingon");
    expect(language()).toBe("fr");
    expect(availableLanguages()).toEqual(["fr", "en"]);
  });

  it("formate les nombres selon la langue", async () => {
    expect(formatNumber(1234.5).replace(/\s/g, " ")).toBe("1 234,5");
    await setLanguage("en");
    expect(formatNumber(1234.5)).toBe("1,234.5");
  });

  it("abrège les nombres d'entités des ports : k, M, Mds", async () => {
    const fr = (n: number) => formatCount(n).replace(/\s/g, " ");
    expect([fr(950), fr(12_345), fr(125_000), fr(999_960), fr(1_601_817), fr(2_100_000_000)]).toEqual([
      "950", "12,3 k", "125 k", "1 M", "1,6 M", "2,1 Mds",
    ]);
    await setLanguage("en");
    expect([formatCount(1_601_817), formatCount(2_100_000_000)]).toEqual(["1.6 M", "2.1 B"]);
  });
});
