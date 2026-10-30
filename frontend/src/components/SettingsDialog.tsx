import { useEffect, useState } from "react";
import { api, currentEmail, currentUser, setCurrentEmail, setCurrentUser } from "../api/client";
import type { AppSettings, AppSettingsValues, Preferences } from "../api/types";
import { language, setLanguage, t } from "../i18n";
import { pickFolder } from "../platform";
import { useAssistant } from "../store/assistant";
import { useEditor } from "../store/editor";
import { AssistantSettings } from "./AssistantSettings";
import { Modal } from "./Dialogs";
import { Loading } from "./Spinner";

type TabId = "general" | "compute" | "network" | "components" | "variables" | "assistant";
type Patch = Partial<{ [K in keyof AppSettingsValues]: AppSettingsValues[K] | null }>;

const TABS: { id: TabId; label: () => string }[] = [
  { id: "general", label: () => t("Général") },
  { id: "compute", label: () => t("Calcul") },
  { id: "network", label: () => t("Réseau") },
  { id: "components", label: () => t("Composants") },
  { id: "variables", label: () => t("Variables globales") },
  { id: "assistant", label: () => t("Assistant") },
];

/** Paramètres de l'application : tout ce qui se règle ici s'applique sans ligne de commande. */
export function SettingsDialog({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<TabId>("general");
  const [data, setData] = useState<AppSettings | null>(null);
  const [error, setError] = useState("");
  const [restart, setRestart] = useState(false);

  const fail = (err: unknown) => setError(err instanceof Error ? err.message : String(err));

  useEffect(() => {
    void api
      .appSettings()
      .then(setData)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  /** Envoie une modification ; la fenêtre affiche ensuite l'état relu côté moteur. */
  const save = async (patch: Patch): Promise<boolean> => {
    setError("");
    try {
      const next = await api.patchAppSettings(patch);
      setData(next);
      if (next.restart_required?.length) setRestart(true);
      return true;
    } catch (err) {
      fail(err);
      return false;
    }
  };

  return (
    <Modal title={t("Paramètres")} onClose={onClose} className="settings">
      <div className="settings-body">
        <div className="settings-tabs" role="tablist" aria-orientation="vertical">
          {TABS.map((x) => (
            <button
              key={x.id}
              role="tab"
              aria-selected={tab === x.id}
              className="ghost"
              onClick={() => {
                setTab(x.id);
                setError("");
              }}
            >
              {x.label()}
            </button>
          ))}
        </div>
        <div className="settings-pane" role="tabpanel">
          {!data && !error && tab !== "assistant" && <Loading text={t("Chargement des paramètres…")} />}
          {tab === "general" && <General data={data} fail={fail} />}
          {tab === "compute" && data && <Compute data={data} save={save} />}
          {tab === "network" && data && <Network data={data} save={save} />}
          {tab === "components" && data && <Components data={data} save={save} />}
          {tab === "variables" && data && <Variables data={data} save={save} />}
          {tab === "assistant" && <AssistantTab />}
        </div>
      </div>
      {restart && (
        <p className="settings-restart" role="status">
          {t("Ce réglage s'applique au prochain démarrage de l'application.")}{" "}
          {window.etlDesktop?.relaunch && <button onClick={() => void window.etlDesktop?.relaunch?.()}>{t("Redémarrer maintenant")}</button>}
        </p>
      )}
      {error && <p className="error">{error}</p>}
      <div className="modal-actions">
        <button onClick={onClose}>{t("Fermer")}</button>
      </div>
    </Modal>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <small className="muted">{hint}</small>}
    </label>
  );
}

// ------------------------------------------------------------------------------------------------ Général
function General({ data, fail }: { data: AppSettings | null; fail: (err: unknown) => void }) {
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [current, setCurrent] = useState(language());
  const [user, setUser] = useState(currentUser());
  const [email, setEmail] = useState(currentEmail());
  const [autoStart, setAutoStart] = useState<boolean | null>(null);
  const [keepRunning, setKeepRunning] = useState<boolean | null>(null);
  const desktop = window.etlDesktop;

  useEffect(() => {
    void api
      .preferences()
      .then(setPrefs)
      .catch(() => undefined);
    void desktop?.getAutoStart?.().then(setAutoStart);
    void desktop?.getKeepRunning?.().then(setKeepRunning);
  }, [desktop]);

  const choose = async (code: string) => {
    setCurrent(code);
    try {
      await api.setLanguage(code); // le réglage de l'utilisateur (settings.json) : retenu aux prochains démarrages
      await setLanguage(code);
      await desktop?.setLanguage(code); // menu et zone de notification de l'application de bureau
      await useEditor.getState().loadPalette(); // la palette est décrite dans la langue choisie
    } catch (err) {
      fail(err);
    }
  };

  return (
    <>
      <Field label={t("Langue de l'interface")} hint={t("Le changement s'applique tout de suite. La langue choisie est retenue pour les prochains démarrages.")}>
        <select value={current} onChange={(e) => void choose(e.target.value)} aria-label={t("Langue de l'interface")}>
          {(prefs?.languages ?? [{ code: current, name: current }]).map((l) => (
            <option key={l.code} value={l.code}>
              {l.name}
            </option>
          ))}
        </select>
      </Field>
      <Field label={t("Utilisateur")} hint={t("Signe vos flux et filtre « Mes runs ».")}>
        <input
          type="text"
          value={user}
          aria-label={t("Utilisateur")}
          onChange={(e) => {
            setUser(e.target.value);
            setCurrentUser(e.target.value);
          }}
        />
      </Field>
      <Field label={t("E-mail de notification")} hint={t("Reçoit un message à la fin d'un run, si le serveur de messagerie est configuré.")}>
        <input
          type="email"
          value={email}
          aria-label={t("E-mail de notification")}
          onChange={(e) => {
            setEmail(e.target.value);
            setCurrentEmail(e.target.value);
          }}
        />
      </Field>
      {autoStart !== null && (
        <label className="check">
          <input
            type="checkbox"
            checked={autoStart}
            onChange={(e) => {
              setAutoStart(e.target.checked);
              void desktop?.setAutoStart?.(e.target.checked);
            }}
          />
          {t("Démarrer avec Windows (fenêtre masquée, pour les planifications)")}
        </label>
      )}
      {keepRunning !== null && (
        <label className="check" title={t("Sinon, fermer la fenêtre arrête complètement Carriole : les planifications ne se lancent plus tant qu'il est fermé.")}>
          <input
            type="checkbox"
            checked={keepRunning}
            onChange={(e) => {
              const on = e.target.checked;
              setKeepRunning(on);
              desktop?.setKeepRunning?.(on).catch((err: unknown) => {
                setKeepRunning(!on);
                fail(err);
              });
            }}
          />
          {t("Laisser Carriole tourner en arrière-plan à la fermeture de la fenêtre (runs, planifications)")}
        </label>
      )}
      {data && (
        <div className="settings-about">
          <strong>Carriole {data.version}</strong>
          <small className="muted" title={data.paths.settings}>
            {t("Données de l'application :")} {data.paths.data}
          </small>
          {desktop?.openAppFolder && (
            <span className="settings-links">
              <button className="link" onClick={() => void desktop.openAppFolder?.("data")}>
                {t("Ouvrir le dossier des données")}
              </button>
              <button className="link" onClick={() => void desktop.openAppFolder?.("logs")}>
                {t("Ouvrir le dossier des journaux")}
              </button>
            </span>
          )}
        </div>
      )}
    </>
  );
}

// ------------------------------------------------------------------------------------------------ Calcul
function Compute({ data, save }: { data: AppSettings; save: (p: Patch) => Promise<boolean> }) {
  const [workers, setWorkers] = useState(String(data.values.workers ?? ""));
  const [memory, setMemory] = useState(String(data.values.memory_budget_mb ?? ""));
  const [cache, setCache] = useState(data.values.cache_dir);
  const number = (text: string) => (text.trim() === "" ? null : Number(text));
  return (
    <>
      <Field label={t("Runs en parallèle")} hint={t("Nombre de runs exécutés en même temps (un par worker). Par défaut : {{n}}.", { n: data.defaults.workers })}>
        <input
          type="number"
          min={1}
          max={32}
          value={workers}
          placeholder={String(data.defaults.workers)}
          aria-label={t("Runs en parallèle")}
          onChange={(e) => setWorkers(e.target.value)}
          onBlur={() => void save({ workers: number(workers) })}
        />
      </Field>
      <Field label={t("Mémoire maximale du moteur (Mo)")} hint={t("Au-delà, les calculs débordent sur le disque, dans le dossier du cache. Vide : 75 % de la mémoire du poste.")}>
        <input
          type="number"
          min={256}
          step={256}
          value={memory}
          aria-label={t("Mémoire maximale du moteur (Mo)")}
          onChange={(e) => setMemory(e.target.value)}
          onBlur={() => void save({ memory_budget_mb: number(memory) })}
        />
      </Field>
      <Field
        label={t("Dossier du cache")}
        hint={t("Résultats des runs et données temporaires des calculs qui dépassent la mémoire : un disque local avec de la place. Actuellement : {{path}}", { path: data.paths.cache })}
      >
        <input
          type="text"
          value={cache}
          placeholder={data.paths.cache}
          aria-label={t("Dossier du cache")}
          onChange={(e) => setCache(e.target.value)}
          onBlur={() => void save({ cache_dir: cache.trim() || null })}
        />
      </Field>
    </>
  );
}

// ------------------------------------------------------------------------------------------------ Réseau
function Network({ data, save }: { data: AppSettings; save: (p: Patch) => Promise<boolean> }) {
  const [proxy, setProxy] = useState(data.values.proxy);
  const [host, setHost] = useState("");
  const hosts = data.values.tile_hosts;
  const add = async () => {
    if (host.trim() && (await save({ tile_hosts: [...hosts, host.trim()] }))) setHost("");
  };
  return (
    <>
      <Field label={t("Proxy")} hint={t("Utilisé pour joindre internet (catalogues STAC, rasters distants, assistant IA). Vide : le proxy de Windows.")}>
        <input type="text" value={proxy} placeholder="http://proxy.exemple.fr:3128" aria-label={t("Proxy")} onChange={(e) => setProxy(e.target.value)} onBlur={() => void save({ proxy: proxy.trim() || null })} />
      </Field>
      <div className="field">
        <span className="field-label">{t("Serveurs de tuiles de fond de plan")}</span>
        <small className="muted">{t("En plus d'OpenStreetMap, Esri et l'IGN. Adresse du serveur seulement, par exemple https://tuiles.exemple.fr")}</small>
        <ul className="settings-list">
          {hosts.map((h) => (
            <li key={h}>
              <code>{h}</code>
              <button className="link" onClick={() => void save({ tile_hosts: hosts.filter((x) => x !== h) })} aria-label={t("Retirer {{name}}", { name: h })}>
                {t("Retirer")}
              </button>
            </li>
          ))}
        </ul>
        <div className="settings-add">
          <input type="text" value={host} placeholder="https://tuiles.exemple.fr" aria-label={t("Adresse du serveur de tuiles")} onChange={(e) => setHost(e.target.value)} onKeyDown={(e) => e.key === "Enter" && void add()} />
          <button onClick={() => void add()} disabled={!host.trim()}>
            {t("Ajouter")}
          </button>
        </div>
      </div>
    </>
  );
}

// ------------------------------------------------------------------------------------------------ Composants
function Components({ data, save }: { data: AppSettings; save: (p: Patch) => Promise<boolean> }) {
  const [dir, setDir] = useState("");
  const dirs = data.values.component_dirs;
  const add = async (value: string) => {
    if (value.trim() && (await save({ component_dirs: [...dirs, value.trim()] }))) setDir("");
  };
  return (
    <div className="field">
      <span className="field-label">{t("Dossiers de composants partagés")}</span>
      <small className="muted">{t("Un composant est un flux réutilisable. Pointez tous les postes vers le même dossier du partage pour les partager.")}</small>
      <ul className="settings-list">
        {dirs.map((d) => (
          <li key={d}>
            <code>{d}</code>
            {data.component_dirs_found[d] === false && <span className="warn-text">{t("introuvable")}</span>}
            <button className="link" onClick={() => void save({ component_dirs: dirs.filter((x) => x !== d) })} aria-label={t("Retirer {{name}}", { name: d })}>
              {t("Retirer")}
            </button>
          </li>
        ))}
        {dirs.length === 0 && <li className="muted">{t("Aucun dossier.")}</li>}
      </ul>
      <div className="settings-add">
        <input type="text" value={dir} placeholder="\\serveur\partage\composants" aria-label={t("Dossier de composants")} onChange={(e) => setDir(e.target.value)} onKeyDown={(e) => e.key === "Enter" && void add(dir)} />
        {window.etlDesktop && <button onClick={() => void pickFolder(t("Dossier de composants")).then((p) => (p ? add(p) : undefined))}>{t("Parcourir…")}</button>}
        <button onClick={() => void add(dir)} disabled={!dir.trim()}>
          {t("Ajouter")}
        </button>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ Variables
function Variables({ data, save }: { data: AppSettings; save: (p: Patch) => Promise<boolean> }) {
  const [rows, setRows] = useState<[string, string][]>(Object.entries(data.values.variables));
  const commit = (next: [string, string][]) => {
    setRows(next);
    const filled = next.filter(([name]) => name.trim());
    void save({ variables: filled.length ? Object.fromEntries(filled.map(([n, v]) => [n.trim(), v])) : null });
  };
  const edit = (i: number, col: 0 | 1, value: string) => setRows(rows.map((r, j) => (j === i ? (col === 0 ? [value, r[1]] : [r[0], value]) : r)));
  return (
    <div className="field">
      <span className="field-label">{t("Variables globales")}</span>
      <small className="muted">
        {t("Disponibles dans tous les flux, par exemple")} <code>{"${global:RACINE}"}</code>
      </small>
      <div className="settings-vars">
        {rows.map(([name, value], i) => (
          <div key={i} className="settings-add">
            <input type="text" value={name} placeholder={t("Nom")} aria-label={t("Nom de la variable {{n}}", { n: i + 1 })} onChange={(e) => edit(i, 0, e.target.value)} onBlur={() => commit(rows)} />
            <input type="text" value={value} placeholder={t("Valeur")} aria-label={t("Valeur de la variable {{n}}", { n: i + 1 })} onChange={(e) => edit(i, 1, e.target.value)} onBlur={() => commit(rows)} />
            <button className="link" onClick={() => commit(rows.filter((_, j) => j !== i))} aria-label={t("Retirer la variable {{n}}", { n: i + 1 })}>
              {t("Retirer")}
            </button>
          </div>
        ))}
      </div>
      <button className="add" onClick={() => setRows([...rows, ["", ""]])}>
        {t("+ Ajouter une variable")}
      </button>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ Assistant
function AssistantTab() {
  const { config, loadConfig } = useAssistant();
  const [open, setOpen] = useState(false);
  useEffect(() => {
    void loadConfig();
  }, [loadConfig]);
  const providers = config?.providers ?? [];
  return (
    <>
      <p>{t("L'assistant lit votre flux, vos fichiers et vos runs, et propose des modifications que vous appliquez d'un clic.")}</p>
      <ul className="settings-list">
        {providers.map((p) => (
          <li key={p.id}>
            <strong>{p.label}</strong> <span className="muted">{p.model}</span>
          </li>
        ))}
        {providers.length === 0 && <li className="muted">{t("Aucun fournisseur. Ajouter Claude, OpenAI, OpenRouter ou un modèle local (Ollama).")}</li>}
      </ul>
      <div>
        <button onClick={() => setOpen(true)}>{t("Configurer un fournisseur")}</button>
      </div>
      {open && (
        <AssistantSettings
          onClose={() => {
            setOpen(false);
            void loadConfig();
          }}
        />
      )}
    </>
  );
}
