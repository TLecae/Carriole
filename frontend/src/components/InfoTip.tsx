import { useId, type ReactNode } from "react";

/**
 * Aide d'un champ : un « i » cerclé à côté du libellé, dont le texte s'affiche au survol ou au clavier (Tab). Un
 * `<span>` sans nom propre : dans un `<label>`, il ne change pas le nom du champ (ni bouton, qui deviendrait le
 * contrôle désigné, ni `aria-label`, qui s'ajouterait au libellé) ; le texte lui est rattaché par `aria-describedby`.
 */
export function InfoTip({ text, children }: { text?: string; children?: ReactNode }) {
  const id = useId();
  if (!text && !children) return null;
  return (
    <span className="info-tip" tabIndex={0} aria-describedby={id}>
      <span className="info-icon" aria-hidden="true">
        i
      </span>
      <span className="info-pop" role="tooltip" id={id}>
        {text}
        {children}
      </span>
    </span>
  );
}
