import { create } from "zustand";
import { api } from "../api/client";
import type { ConnectionInfo } from "../api/types";

interface ConnectionsState {
  list: ConnectionInfo[];
  loaded: boolean;
  refresh: () => Promise<void>;
}

/** Connexions nommées connues du serveur (sans mot de passe) : alimente le champ « Connexion » des modificateurs. */
export const useConnections = create<ConnectionsState>((set) => ({
  list: [],
  loaded: false,
  refresh: async () => {
    try {
      set({ list: await api.connections(), loaded: true });
    } catch {
      set({ loaded: true });
    }
  },
}));
