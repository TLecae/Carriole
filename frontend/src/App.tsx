import { ReactFlowProvider } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useEffect } from "react";
import { AssistantPanel } from "./components/AssistantPanel";
import { BottomPanel } from "./components/BottomPanel";
import { Canvas } from "./components/Canvas";
import { ConnectionBanner } from "./components/ConnectionBanner";
import { Home } from "./components/Home";
import { PickerHost } from "./components/PickerHost";
import { GlobalDialogs } from "./components/GlobalDialogs";
import { api } from "./api/client";
import { Palette } from "./components/Palette";
import { RightPanel } from "./components/RightPanel";
import { TopBar } from "./components/TopBar";
import { applyRoute, newFlowFromHome, openFlowFileFromHome } from "./navigation";
import { useEditor } from "./store/editor";
import { useAssistant } from "./store/assistant";
import { useUi } from "./store/ui";
import "./styles.css";
import { setLanguage, useLanguage } from "./i18n";

/** Raccourcis clavier : enregistrer, annuler/rétablir, copier/coller, activer/désactiver. */
function useShortcuts() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (useUi.getState().view !== "editor") return;
      const t = e.target as HTMLElement | null;
      // champ de saisie, ou table de l'inspecteur (elle a sa propre copie) : les raccourcis du flux ne s'appliquent pas
      const typing = !!t?.closest("input, textarea, select, [contenteditable=true], [data-own-keys]");
      const s = useEditor.getState();
      const mod = e.ctrlKey || e.metaKey;
      const key = e.key.toLowerCase();
      if (mod && key === "s") {
        e.preventDefault();
        void (e.shiftKey ? s.saveAs() : s.save());
      } else if (!typing && mod && key === "z") {
        e.preventDefault();
        if (e.shiftKey) s.redo();
        else s.undo();
      } else if (!typing && mod && key === "y") {
        e.preventDefault();
        s.redo();
      } else if (!typing && mod && key === "c" && !window.getSelection()?.toString()) void s.copy(); // texte sélectionné : copie normale
      else if (!typing && mod && key === "v") {
        e.preventDefault();
        void s.paste();
      } else if (e.altKey && key === "d") {
        e.preventDefault();
        s.toggleEnabled();
      }
    };
    window.addEventListener("keydown", onKey);
    const warn = (e: BeforeUnloadEvent) => {
      if (useEditor.getState().dirty) e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("beforeunload", warn);
    };
  }, []);
}

/**
 * Signale au moteur le run affiché par l'éditeur (inspecteur, reprise des nœuds inchangés) : son cache est gardé tant
 * que le flux est ouvert. Flux fermé ou remplacé, page fermée (ou plantée, faute de signal) : le moteur le libère.
 */
function useCacheHold() {
  useEffect(() => {
    const page = crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
    const send = () => {
      const id = useEditor.getState().run.id;
      api.holdCache(page, id ? [id] : []).catch(() => undefined); // moteur injoignable : au prochain signal
    };
    send();
    const timer = setInterval(send, 20_000);
    const unsubscribe = useEditor.subscribe((s, prev) => {
      if (s.run.id !== prev.run.id) send();
    });
    const release = () => navigator.sendBeacon("/api/cache/release", new Blob([JSON.stringify({ page })], { type: "text/plain" }));
    window.addEventListener("pagehide", release);
    return () => {
      clearInterval(timer);
      unsubscribe();
      window.removeEventListener("pagehide", release);
    };
  }, []);
}

function Toast() {
  const notice = useEditor((s) => s.notice);
  const setNotice = useEditor.getState().setNotice;
  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(null), notice.kind === "error" ? 8000 : 2500);
    return () => clearTimeout(t);
  }, [notice, setNotice]);
  if (!notice) return null;
  return (
    <div className={`toast ${notice.kind}`} role="status" onClick={() => setNotice(null)}>
      {notice.text}
    </div>
  );
}

export function App() {
  useShortcuts();
  useCacheHold();
  const view = useUi((s) => s.view);
  const lang = useLanguage();
  const bottomHeight = useUi((s) => s.bottomHeight);
  const assistantOpen = useAssistant((s) => s.open);
  useEffect(() => {
    // La palette d'abord : ouvrir un flux en a besoin pour décrire ses nœuds. Ensuite l'adresse décide de la vue.
    // Le mode de stockage (fichiers ou base) décide de l'accueil et de l'enregistrement : à connaître avant la route.
    // La langue choisie par l'utilisateur (réglage de l'application) d'abord : tout ce qui suit s'affiche dans cette langue.
    void api
      .preferences()
      .then((p) => (p.configured ? setLanguage(p.language) : undefined))
      .catch(() => undefined)
      .then(() => api.version())
      .then((v) => useUi.getState().setFlowStorage(v.flow_storage ?? "db"))
      .catch(() => undefined)
      .then(() => useEditor.getState().loadPalette())
      .then(applyRoute)
      .then(() => {
        // Application de bureau : le menu et la zone de notification demandent à la page d'ouvrir un flux
        // (abonnement après la palette : ouvrir un flux en a besoin ; les demandes arrivées avant sont en attente).
        window.etlDesktop?.onOpenFile((path) => void openFlowFileFromHome(path));
        window.etlDesktop?.onNewFlow(newFlowFromHome);
      });
    window.addEventListener("popstate", applyRoute);
    return () => window.removeEventListener("popstate", applyRoute);
  }, []);

  return (
    <ReactFlowProvider key={lang}>
      {view === "home" ? (
        <>
          <Home />
          <Toast />
        </>
      ) : (
        <div className="app" style={{ ["--bottom" as string]: `${bottomHeight}px` }}>
          <TopBar />
          <div className={`main${assistantOpen ? " with-assistant" : ""}`}>
            <Palette />
            <Canvas />
            <RightPanel />
            {assistantOpen && <AssistantPanel />}
          </div>
          <BottomPanel />
          <Toast />
        </div>
      )}
      <PickerHost />
      <GlobalDialogs />
      <ConnectionBanner />
    </ReactFlowProvider>
  );
}
