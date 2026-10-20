import { useEffect, useState } from "react";
import { t } from "../i18n";

/** Petit cercle qui tourne : quelque chose se charge ou se calcule. */
export function Spinner({ size = 14, label }: { size?: number; label?: string }) {
  return <span className="spinner" role="status" aria-label={label ?? t("Chargement…")} style={{ width: size, height: size }} />;
}

/** Bloc de chargement d'un panneau (inspecteur, listes…) : cercle et texte, centré. */
export function Loading({ text, overlay = false }: { text?: string; overlay?: boolean }) {
  return (
    <div className={overlay ? "loading overlay" : "loading"} data-testid="loading">
      <Spinner size={18} />
      <span>{text ?? t("Chargement…")}</span>
    </div>
  );
}

/** Secondes écoulées depuis `since` (Date.now()), mises à jour chaque seconde. */
export function Elapsed({ since }: { since: number }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return <>{Math.max(0, Math.floor((now - since) / 1000))} s</>;
}
