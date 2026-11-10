import { create } from "zustand";
import { api, ApiError, setSessionUser, setUnauthorizedHandler } from "../api/client";
import type { AuthInfo, Me } from "../api/types";

/**
 * Qui utilise l'interface (ADR 0052). Poste de bureau : l'utilisateur du poste, administrateur, sans connexion.
 * Mode serveur : la page de connexion tant qu'aucune session n'est ouverte ; une réponse 401 y ramène.
 */
interface SessionState {
  state: "checking" | "login" | "ready";
  me: Me | null;
  info: AuthInfo | null;
  check: () => Promise<void>;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

export const useSession = create<SessionState>((set) => ({
  state: "checking",
  me: null,
  info: null,

  check: async () => {
    try {
      const me = await api.me();
      setSessionUser(me.mode === "server" ? me.name : "");
      set({ me, state: "ready" });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        const info = await api.authInfo().catch(() => null);
        set({ state: "login", me: null, info });
      } else set({ state: "ready" }); // ancien moteur sans /auth/me, ou moteur injoignable : l'interface le dira
    }
  },

  login: async (username, password) => {
    const me = await api.login(username, password);
    setSessionUser(me.name);
    set({ me: { ...me, mode: "server", logout: true }, state: "ready" });
  },

  logout: async () => {
    await api.logout().catch(() => undefined);
    setSessionUser("");
    location.reload(); // rien de la session précédente ne reste en mémoire
  },
}));

setUnauthorizedHandler(() => {
  const s = useSession.getState();
  if (s.state === "ready" && s.me?.mode === "server") {
    setSessionUser("");
    void api
      .authInfo()
      .catch(() => null)
      .then((info) => useSession.setState({ state: "login", me: null, info: info ?? s.info }));
  }
});

/** Mode serveur et utilisateur non administrateur : réglages de la machine en lecture seule. */
export const isAdmin = (): boolean => useSession.getState().me?.admin ?? true;
export const serverMode = (): boolean => useSession.getState().me?.mode === "server";
