import { createContext, useCallback, useContext, useEffect, useId, useRef, useState } from "react";
import { Icon } from "./Icon";

const MenuContext = createContext<() => void>(() => undefined);

/**
 * Menu déroulant de la barre du haut. Se ferme au clic ailleurs, à Échap et après un choix ; flèches et Début/Fin
 * parcourent les entrées (lecteurs d'écran : rôles menu et menuitem).
 */
export function Menu({ label, children, className = "" }: { label: string; children: React.ReactNode; className?: string }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => !root.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  const items = () => Array.from(root.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)') ?? []);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape" && open) {
      e.stopPropagation();
      setOpen(false);
      trigger.current?.focus();
      return;
    }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    if (!open) return setOpen(true);
    const all = items();
    if (all.length === 0) return;
    const at = all.indexOf(document.activeElement as HTMLButtonElement);
    const next = e.key === "Home" ? 0 : e.key === "End" ? all.length - 1 : e.key === "ArrowDown" ? (at + 1) % all.length : (at - 1 + all.length) % all.length;
    all[next].focus();
  };

  return (
    <div className={`menu ${className}`.trim()} ref={root} onKeyDown={onKeyDown}>
      <button ref={trigger} type="button" className="menu-trigger ghost" aria-haspopup="menu" aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => setOpen(!open)}>
        {label}
        <Icon name="chevron" size={12} />
      </button>
      {open && (
        <MenuContext.Provider value={close}>
          <div className="menu-panel" role="menu" id={id} aria-label={label}>
            {children}
          </div>
        </MenuContext.Provider>
      )}
    </div>
  );
}

export function MenuItem({ onSelect, disabled, shortcut, checked, danger, title, children }: { onSelect: () => void; disabled?: boolean; shortcut?: string; checked?: boolean; danger?: boolean; title?: string; children: React.ReactNode }) {
  const close = useContext(MenuContext);
  return (
    <button
      type="button"
      role={checked === undefined ? "menuitem" : "menuitemcheckbox"}
      aria-checked={checked}
      className={`menu-item${danger ? " danger-text" : ""}`}
      disabled={disabled}
      title={title}
      onClick={() => {
        close();
        onSelect();
      }}
    >
      <span className="menu-check">{checked ? <Icon name="check" size={12} /> : null}</span>
      <span className="menu-text">{children}</span>
      {shortcut && <kbd aria-hidden="true">{shortcut}</kbd>}
    </button>
  );
}

export const MenuSeparator = () => <div className="menu-separator" role="separator" />;
export const MenuLabel = ({ children }: { children: React.ReactNode }) => (
  <div className="menu-label" role="presentation">
    {children}
  </div>
);
