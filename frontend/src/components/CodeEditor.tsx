import { python } from "@codemirror/lang-python";
import { sql } from "@codemirror/lang-sql";
import { EditorState } from "@codemirror/state";
import { EditorView, basicSetup } from "codemirror";
import { useEffect, useRef } from "react";

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
