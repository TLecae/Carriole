import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// En développement, le proxy de Vite relaie les routes /api et /tiles vers le backend (`etl serve`).
const api = process.env.VITE_API_URL ?? "http://127.0.0.1:8000"; // adresse donnée par `etl status`

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": api, "/tiles": api } },
  test: { environment: "node" },
});
