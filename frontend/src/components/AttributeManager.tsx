import { useEffect, useRef, useState, type ReactNode } from "react";
import { t } from "../i18n";
import { actionOf, norm, rowsFromRules, rulesFromRows, type Row, type Rule } from "../model/attributeManager";
import type { HelperSource } from "../model/fieldHelper";
import { Modal } from "./Dialogs";
import { EditorButton, QueryEditor } from "./SqlEditor";

const ACTION_LABEL: Record<ReturnType<typeof actionOf>, () => string> = {
  none: () => "",
  rename: () => t("Renommer"),
  set: () => t("Définir la valeur"),
  "rename-set": () => t("Renommer, définir la valeur"),
  remove: () => t("Supprimer"),
  create: () => t("Créer"),
};

/**
 * « Gérer des attributs » : tous les attributs reçus dans un tableau (entrée, sortie, valeur, action), comme une vue
 * d'ensemble ; on renomme en changeant la sortie, on calcule en donnant une valeur. Les règles ne tiennent pas dans
 * le tableau (ordre qui compte) : `fallback`, la liste des règles.
 */
export function AttributeManager({ value, received, helper, onChange, fallback }: { value: Partial<Rule>[]; received: string[]; helper?: HelperSource; onChange: (rules: Rule[]) => void; fallback: ReactNode }) {
  const [rows, setRows] = useState<Row[] | null>(() => rowsFromRules(value, received));
  const emitted = useRef(JSON.stringify(value.map(norm)));
  const [big, setBig] = useState(false);
  const receivedKey = received.join("\n");

  // règles changées ailleurs (annuler, assistant) ou attributs reçus connus plus tard : le tableau se refait ; ses
  // propres changements, non (une ligne renommée qui prend une valeur resterait sinon coupée en deux pendant la saisie)
  useEffect(() => {
    const key = JSON.stringify(value.map(norm));
    if (key === emitted.current) return;
    emitted.current = key;
    setRows(rowsFromRules(value, received));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `received` est suivi par sa clé
  }, [value]);
  useEffect(() => {
    setRows(rowsFromRules(value, received));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [receivedKey]);

  if (!rows)
    return (
      <>
        <p className="muted">{t("Ces règles dépendent de leur ordre : elles restent sous forme de liste.")}</p>
        {fallback}
      </>
    );

  const update = (next: Row[]) => {
    setRows(next);
    const rules = rulesFromRows(next);
    emitted.current = JSON.stringify(rules);
    onChange(rules);
  };
  const table = <AttributeTable rows={rows} received={received} update={update} helper={helper} />;
  return (
    <div className="attr-manager">
      <div className="attr-manager-bar">
        <span className="muted">{t("{{count}} attribut(s)", { count: rows.length })}</span>
        <button type="button" onClick={() => setBig(true)}>
          {t("Agrandir")}
        </button>
      </div>
      {table}
      {big && (
        <Modal title={t("Gérer des attributs")} onClose={() => setBig(false)} className="wide attr-manager-modal">
          {table}
          <div className="modal-actions">
            <button onClick={() => setBig(false)}>{t("Fermer")}</button>
          </div>
        </Modal>
      )}
    </div>
  );
}

function AttributeTable({ rows, received, update, helper }: { rows: Row[]; received: string[]; update: (rows: Row[]) => void; helper?: HelperSource }) {
  const [filter, setFilter] = useState("");
  const [editing, setEditing] = useState<number | null>(null); // valeur ouverte dans l'éditeur (attributs, valeurs)
  const known = new Set(received);
  const patch = (i: number, p: Partial<Row>) => update(rows.map((r, j) => (j === i ? { ...r, ...p } : r)));
  const outputs = rows.filter((r) => !r.removed).map((r) => r.output);
  const needle = filter.trim().toLowerCase();
  return (
    <div className="attr-table-wrap">
      {rows.length > 6 && <input type="search" className="attr-filter" value={filter} placeholder={t("Filtrer les attributs…")} aria-label={t("Filtrer les attributs…")} onChange={(e) => setFilter(e.target.value)} />}
      <table className="attr-table">
        <thead>
          <tr>
            <th>{t("Attribut reçu")}</th>
            <th>{t("Attribut en sortie")}</th>
            <th>{t("Valeur")}</th>
            <th>{t("Action")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            if (needle && !`${r.input ?? ""} ${r.output}`.toLowerCase().includes(needle)) return null;
            const action = actionOf(r);
            const absent = r.input !== null && received.length > 0 && !known.has(r.input);
            const twice = !r.removed && r.output !== "" && outputs.indexOf(r.output) !== outputs.lastIndexOf(r.output);
            return (
              <tr key={i} className={`${action}${absent ? " absent" : ""}`}>
                <td className="attr-in" title={absent ? t("Absent des attributs reçus") : undefined}>
                  {r.input ?? <span className="muted">{t("(nouveau)")}</span>}
                </td>
                <td>
                  <input
                    type="text"
                    aria-label={t("Attribut en sortie, ligne {{n}}", { n: i + 1 })}
                    className={twice || (!r.removed && !r.output) ? "invalid" : undefined}
                    title={twice ? t("Nom en double en sortie") : undefined}
                    value={r.output}
                    disabled={r.removed}
                    placeholder={t("nom")}
                    onChange={(e) => patch(i, { output: e.target.value })}
                  />
                </td>
                <td>
                  <div className="attr-value-cell">
                    <input
                      type="text"
                      className="attr-value"
                      aria-label={t("Valeur, ligne {{n}}", { n: i + 1 })}
                      value={r.expression}
                      disabled={r.removed}
                      placeholder={r.input === null ? t("expression SQL") : t("(inchangée)")}
                      onChange={(e) => patch(i, { expression: e.target.value })}
                    />
                    {!r.removed && <EditorButton compact onClick={() => setEditing(i)} />}
                  </div>
                </td>
                <td className="attr-action">
                  <span>{ACTION_LABEL[action]()}</span>
                  {r.input === null ? (
                    <button type="button" className="ghost icon-only" aria-label={t("Retirer la ligne {{n}}", { n: i + 1 })} title={t("Retirer")} onClick={() => update(rows.filter((_, j) => j !== i))}>
                      ×
                    </button>
                  ) : (
                    <button type="button" className="ghost" aria-pressed={r.removed} title={r.removed ? t("Garder cet attribut") : t("Supprimer cet attribut")} onClick={() => patch(i, { removed: !r.removed })}>
                      {r.removed ? t("Garder") : t("Supprimer")}
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {editing !== null && rows[editing] && (
        <QueryEditor
          label={t("Valeur de {{name}}", { name: rows[editing].output || rows[editing].input || "?" })}
          value={rows[editing].expression}
          onChange={(v) => patch(editing, { expression: v })}
          mode="expression"
          helper={helper}
          onClose={() => setEditing(null)}
        />
      )}
      {!received.length && <p className="muted">{t("Attributs reçus inconnus : relier l'entrée, ou lancer le flux en amont.")}</p>}
      <button type="button" className="add" onClick={() => update([...rows, { input: null, output: "", expression: "", removed: false }])}>
        {t("+ Ajouter un attribut")}
      </button>
    </div>
  );
}
