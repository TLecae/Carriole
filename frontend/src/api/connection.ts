/**
 * Joignabilité du moteur. Une requête restée sans réponse (moteur arrêté, en cours de relance par l'application)
 * passe la page « hors ligne » : elle interroge alors /api/health jusqu'au retour, sans se recharger (le flux en cours
 * d'édition n'est pas perdu). L'application de bureau relance le moteur à la même adresse, avec le même jeton.
 */
type Listener = (online: boolean) => void;

const PROBE_MS = 2000;
let online = true;
let timer: ReturnType<typeof setInterval> | null = null;
const listeners = new Set<Listener>();

export const isOnline = (): boolean => online;

export function onConnection(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function update(value: boolean): void {
  if (online === value) return;
  online = value;
  listeners.forEach((l) => l(value));
}

async function probe(): Promise<void> {
  try {
    const res = await fetch("/api/health", { cache: "no-store" });
    if (res.ok) reportOnline();
  } catch {
    /* toujours injoignable */
  }
}

export function reportOffline(): void {
  update(false);
  timer ??= setInterval(() => void probe(), PROBE_MS);
}

export function reportOnline(): void {
  if (timer !== null) {
    clearInterval(timer);
    timer = null;
  }
  update(true);
}
