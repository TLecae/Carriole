import { useEffect, useState, useSyncExternalStore } from "react";
import { isOnline, onConnection } from "../api/connection";
import { t } from "../i18n";
import { Spinner } from "./Spinner";

/** Bandeau en haut de la page quand le moteur ne répond plus, puis « reconnecté » quelques secondes à son retour. */
export function ConnectionBanner() {
  const online = useSyncExternalStore(onConnection, isOnline);
  const [back, setBack] = useState(false);
  const [wasOffline, setWasOffline] = useState(false);
  useEffect(() => {
    if (!online) {
      setWasOffline(true);
      setBack(false);
      return;
    }
    if (!wasOffline) return;
    setBack(true);
    const timer = setTimeout(() => setBack(false), 4000);
    return () => clearTimeout(timer);
  }, [online, wasOffline]);

  if (!online) {
    return (
      <div className="connection-banner offline" role="alert" data-testid="connection-banner">
        <Spinner size={14} label={t("Reconnexion…")} />
        <strong>{t("Le moteur ne répond plus : reconnexion en cours…")}</strong>
        <span>{t("Vos modifications restent dans cette fenêtre : ne la fermez pas.")}</span>
      </div>
    );
  }
  if (!back) return null;
  return (
    <div className="connection-banner back" role="status" data-testid="connection-banner">
      {t("Moteur reconnecté.")}
    </div>
  );
}
