import { python } from "@codemirror/lang-python";
import { sql } from "@codemirror/lang-sql";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { EditorState } from "@codemirror/state";
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
}: {
  value: string;
  language: "python" | "sql";
  onChange: (value: string) => void;
  label?: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const latest = useRef(onChange);
  latest.current = onChange;

  useEffect(() => {
    if (!host.current) return;
    const editor = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: value,
        extensions: [
          basicSetup,
          codeLook,
          language === "python" ? python() : sql(),
          EditorView.lineWrapping,
          EditorView.contentAttributes.of({ "aria-label": label ?? language, spellcheck: "false" }),
          EditorView.updateListener.of((update) => {
            if (update.docChanged) latest.current(update.state.doc.toString());
          }),
        ],
      }),
    });
    view.current = editor;
    return () => {
      editor.destroy();
      view.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- le contenu est suivi par l'effet ci-dessous
  }, [language, label]);

  // annuler / rétablir ou ouvrir un autre nœud change le texte de l'extérieur
  useEffect(() => {
    const editor = view.current;
    if (editor && editor.state.doc.toString() !== value) {
      editor.dispatch({ changes: { from: 0, to: editor.state.doc.length, insert: value } });
    }
  }, [value]);

  return <div className="code-editor" ref={host} data-testid="code-editor" />;
}
