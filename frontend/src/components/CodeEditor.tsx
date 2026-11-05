import { python } from "@codemirror/lang-python";
import { PLSQL, PostgreSQL, StandardSQL, sql } from "@codemirror/lang-sql";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { linter, lintGutter } from "@codemirror/lint";
import { Compartment, EditorState } from "@codemirror/state";
import type { SqlCheckResult } from "../api/types";
import { tags } from "@lezer/highlight";
import { EditorView, basicSetup } from "codemirror";
import { useEffect, useRef } from "react";

// couleurs des jetons (styles.css) : elles suivent le thème clair ou sombre sans recréer l'éditeur
const highlight = HighlightStyle.define([
  { tag: [tags.keyword, tags.operatorKeyword, tags.modifier], color: "var(--cm-keyword)" },
  { tag: [tags.string, tags.special(tags.string)], color: "var(--cm-string)" },
  { tag: [tags.number, tags.bool, tags.null, tags.atom], color: "var(--cm-number)" },
  { tag: [tags.comment, tags.lineComment, tags.blockComment], color: "var(--cm-comment)", fontStyle: "italic" },
  { tag: [tags.typeName, tags.className, tags.standard(tags.name)], color: "var(--cm-type)" },
  { tag: [tags.function(tags.variableName), tags.function(tags.propertyName)], color: "var(--cm-function)" },
]);
const look = EditorView.theme({
  "&": { color: "var(--text)", backgroundColor: "var(--panel)" },
  ".cm-gutters": { backgroundColor: "var(--surface-2)", color: "var(--muted)", borderRight: "1px solid var(--line)" },
  ".cm-activeLine": { backgroundColor: "var(--hover)" },
  ".cm-activeLineGutter": { backgroundColor: "var(--hover)" },
  ".cm-cursor": { borderLeftColor: "var(--text)" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground": { backgroundColor: "var(--selected) !important" },
  ".cm-tooltip": { backgroundColor: "var(--panel)", color: "var(--text)", border: "1px solid var(--line)" },
  ".cm-tooltip-autocomplete ul li[aria-selected]": { backgroundColor: "var(--selected)", color: "var(--text)" },
});
export const codeLook = [look, syntaxHighlighting(highlight)];

/**
 * Éditeur de code (CodeMirror 6) pour les paramètres de type code : Python (PythonScript) et SQL (ExecuteSql).
 * Chargé à la demande : il pèse plus que le reste du formulaire.
 */
export default function CodeEditor({
  value,
  language,
  onChange,
  label,
  dialect,
  schema,
  check,
  tall = false,
  inserter,
}: {
  value: string;
  language: "python" | "sql";
  onChange: (value: string) => void;
  label?: string;
  dialect?: string; // SQL : « postgres », « oracle », « duckdb » (mots-clés et fonctions proposés)
  schema?: Record<string, string[]>; // SQL : tables et leurs colonnes, proposées à la saisie
  check?: (sql: string) => Promise<SqlCheckResult>; // SQL : vérification par le serveur, soulignée dans le texte
  tall?: boolean; // éditeur en surcouche : toute la hauteur disponible
  inserter?: { current: ((text: string) => void) | null }; // insère un texte au curseur (aide à la saisie)
}) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const latest = useRef(onChange);
  latest.current = onChange;
  const checker = useRef(check);
  checker.current = check;
  const languageSlot = useRef(new Compartment());

  const languageSupport = () =>
    language === "python"
      ? python()
      : sql({
          dialect: dialect === "oracle" ? PLSQL : dialect ? PostgreSQL : StandardSQL,
          schema,
          defaultTable: schema && Object.keys(schema).length === 1 && "data" in schema ? "data" : undefined, // filtre d'un fichier : ses colonnes sans préfixe
          upperCaseKeywords: true,
        });

  useEffect(() => {
    if (!host.current) return;
    const editor = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: value,
        extensions: [
          basicSetup,
          codeLook,
          languageSlot.current.of(languageSupport()),
          ...(check
            ? [
                lintGutter(),
                linter(
                  async (v) => {
                    const text = v.state.doc.toString();
                    const run = checker.current;
                    if (!run) return [];
                    let r: SqlCheckResult;
                    try {
                      r = await run(text);
                    } catch {
                      return []; // serveur ou base injoignable : rien à souligner
                    }
                    if (r.ok || !r.error || v.state.doc.toString() !== text) return [];
                    const doc = v.state.doc;
                    const line = doc.line(Math.min(Math.max(r.line ?? 1, 1), doc.lines));
                    const from = r.column ? Math.min(line.from + r.column - 1, line.to) : line.from;
                    const to = r.column ? Math.min(Math.max(doc.sliceString(from, line.to).search(/[\s,;()]|$/) + from, from + 1), line.to) : line.to;
                    return [{ from, to: Math.max(to, from), severity: "error", message: r.error }];
                  },
                  { delay: 900 },
                ),
              ]
            : []),
          EditorView.lineWrapping,
          EditorView.contentAttributes.of({ "aria-label": label ?? language, spellcheck: "false" }),
          EditorView.updateListener.of((update) => {
            if (update.docChanged) latest.current(update.state.doc.toString());
          }),
        ],
      }),
    });
    view.current = editor;
    if (inserter)
      inserter.current = (text) => {
        const { from, to } = editor.state.selection.main;
        editor.dispatch({ changes: { from, to, insert: text }, selection: { anchor: from + text.length } });
        editor.focus();
      };
    return () => {
      editor.destroy();
      view.current = null;
      if (inserter) inserter.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- le contenu est suivi par l'effet ci-dessous
  }, [language, label, !!check]);

  // tables et colonnes lues après l'ouverture : la complétion les prend sans recréer l'éditeur
  useEffect(() => {
    view.current?.dispatch({ effects: languageSlot.current.reconfigure(languageSupport()) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dialect, schema]);

  // annuler / rétablir ou ouvrir un autre nœud change le texte de l'extérieur
  useEffect(() => {
    const editor = view.current;
    if (editor && editor.state.doc.toString() !== value) {
      editor.dispatch({ changes: { from: 0, to: editor.state.doc.length, insert: value } });
    }
  }, [value]);

  return <div className={tall ? "code-editor tall" : "code-editor"} ref={host} data-testid="code-editor" />;
}
