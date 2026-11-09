import { type InputHTMLAttributes, type Ref, useId, useState } from "react";

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "list"> & {
  value: string;
  options: string[];
  onChange: (value: string) => void;
  inputRef?: Ref<HTMLInputElement>;
};

/** Valeurs proposées que la saisie contient (toutes tant que rien n'est tapé, ou si la valeur en est une). */
export function comboMatches(options: string[], value: string, typed: boolean): string[] {
  const needle = value.trim().toLowerCase();
  if (!typed || !needle || options.includes(value)) return options;
  return options.filter((o) => o.toLowerCase().includes(needle));
}

/**
 * Saisie libre avec une liste de valeurs proposées, aux couleurs de l'application (la liste native d'un `datalist`
 * suit le système : fond noir, texte gras). Flèches, Entrée et Échap au clavier ; un clic choisit.
 */
export function Combo({ value, options, onChange, inputRef, className, onFocus, onBlur, onKeyDown, ...rest }: Props) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState(false);
  const [active, setActive] = useState(-1);
  const listId = useId();
  const shown = comboMatches(options, value, typed);
  const visible = open && shown.length > 0;
  const pick = (choice: string) => {
    onChange(choice);
    setOpen(false);
    setTyped(false);
  };

  return (
    <div className={`combo${className ? ` ${className}` : ""}`}>
      <input
        {...rest}
        ref={inputRef}
        type="text"
        role="combobox"
        aria-expanded={visible}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={visible && active >= 0 ? `${listId}-${active}` : undefined}
        autoComplete="off"
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
          setTyped(true);
          setOpen(true);
          setActive(-1);
        }}
        onFocus={(e) => {
          setOpen(true);
          setTyped(false);
          onFocus?.(e);
        }}
        onBlur={(e) => {
          setOpen(false);
          onBlur?.(e);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" || e.key === "ArrowUp") {
            e.preventDefault();
            setOpen(true);
            const step = e.key === "ArrowDown" ? 1 : -1;
            setActive((a) => (shown.length ? (a + step + shown.length) % shown.length : -1));
          } else if (e.key === "Enter" && visible && active >= 0 && active < shown.length) {
            e.preventDefault();
            pick(shown[active]);
          } else if (e.key === "Escape" && visible) {
            e.preventDefault();
            e.stopPropagation(); // ferme la liste, pas la fenêtre qui contient le champ
            setOpen(false);
          }
          onKeyDown?.(e);
        }}
      />
      {visible && (
        <ul className="combo-list" id={listId} role="listbox">
          {shown.map((o, i) => (
            <li
              key={o}
              id={`${listId}-${i}`}
              title={o}
              role="option"
              aria-selected={o === value}
              className={i === active ? "active" : undefined}
              onMouseDown={(e) => e.preventDefault()} // le champ garde le focus : le clic choisit
              onClick={() => pick(o)}
              onMouseEnter={() => setActive(i)}
            >
              {o}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
