import { useState } from "react";
import { ApiError } from "../api/client";
import { useSession } from "../store/session";
import { t } from "../i18n";

/** Page de connexion du mode serveur (ADR 0052, 0059) : identifiant et mot de passe d'un compte du serveur. */
export function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await useSession.getState().login(username.trim(), password);
    } catch (err) {
      setPassword("");
      setError(err instanceof ApiError || err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="login-page">
      <form className="login-card" onSubmit={(e) => void submit(e)} data-testid="login-form">
        <h1>Carriole</h1>
        <label>
          {t("Identifiant")}
          <input autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
        </label>
        <label>
          {t("Mot de passe")}
          <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="primary" disabled={busy || !username.trim() || !password}>
          {busy ? t("Connexion…") : t("Se connecter")}
        </button>
      </form>
    </main>
  );
}
