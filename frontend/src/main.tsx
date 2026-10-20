import { Component, StrictMode, type ErrorInfo, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { t } from "./i18n";

/** Une erreur d'affichage ne doit pas laisser un écran blanc : on l'explique et on propose de recharger. */
class Boundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(error, info.componentStack);
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div style={{ padding: "2rem", fontFamily: "system-ui" }} role="alert">
        <h1>{t("Une erreur d'affichage est survenue")}</h1>
        <p>{this.state.error.message}</p>
        <p>{t("Vos modifications non enregistrées sont conservées tant que la page n'est pas rechargée.")}</p>
        <button onClick={() => this.setState({ error: null })}>{t("Réessayer")}</button>{" "}
        <button onClick={() => location.reload()}>{t("Recharger la page")}</button>
      </div>
    );
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Boundary>
      <App />
    </Boundary>
  </StrictMode>,
);