import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, api } from "../api/client";
import type { FileListing } from "../api/types";
import { formatSize, join, startFolder } from "../model/files";
import { Modal } from "./Dialogs";
import { Loading } from "./Spinner";
import { formatDate, t } from "../i18n";

interface Props {
  mode: "read" | "write";
  value: string;
  title?: string;
  /** Nom proposé à l'enregistrement. */
  defaultName?: string;
  /** Extensions montrées (ex. ".json") ; remplace le filtre « formats lus par Carriole ». */
  extensions?: string[];
  onPick: (path: string) => void;
  onClose: () => void;
}

/**
 * Explorateur des fichiers du serveur. Le navigateur ne voit pas ces fichiers : on les parcourt via l'API, qui ne
 * montre que les dossiers autorisés (lecture : sources, entrées, sorties ; écriture : sorties).
 */
export function FilePicker({ mode, value, title, defaultName, extensions, onPick, onClose }: Props) {
  const start = useMemo(() => startFolder(value), [value]);
  const [listing, setListing] = useState<FileListing | null>(null);
  const [reading, setReading] = useState(true); // dossier en cours de lecture (un partage réseau peut être lent)
  const [error, setError] = useState<string | null>(null);
  const [recognized, setRecognized] = useState(mode === "read" && !extensions);
  const [selected, setSelected] = useState<string | null>(null);
  const [name, setName] = useState(mode === "write" ? (defaultName ?? start.name) : "");
  const [filter, setFilter] = useState("");
  const [address, setAddress] = useState("");

  const go = useCallback(
    async (path: string, fallbackToRoots = false) => {
      setReading(true);
      try {
        const next = await api.files(path, mode, recognized, extensions);
        // Un seul emplacement possible (ex. les sorties en écriture) : inutile de faire cliquer dessus.
        if (!path && next.roots.length === 1 && next.roots[0].available) return go(next.roots[0].path);
        setListing(next);
        setAddress(next.path);
        setSelected(null);
        setFilter("");
        setError(null);
      } catch (err) {
        if (fallbackToRoots) return go("");
        setError(err instanceof ApiError ? err.message : t("Dossier illisible"));
      } finally {
        setReading(false);
      }
    },
    [mode, recognized, extensions],
  );

  // Ouverture : dans le dossier du champ s'il est accessible, sinon à la liste des dossiers autorisés.
  // Changer le filtre « formats lus » recharge le dossier courant.
  useEffect(() => {
    void go(listing?.path ?? start.dir, listing === null);
  }, [go]); // eslint-disable-line react-hooks/exhaustive-deps

  const here = listing?.path ?? "";
  const entries = (listing?.entries ?? []).filter((e) => !filter || e.name.toLowerCase().includes(filter.toLowerCase()));
  const atRoots = here === "";
  const isRoot = listing?.roots.some((r) => r.path === here) ?? false;
  const chosen = mode === "write" ? (here && name.trim() ? join(here, name.trim()) : null) : selected;
  const pick = (path: string | null) => path && onPick(path);

  return (
    <Modal title={title ?? (mode === "write" ? t("Choisir où écrire") : t("Choisir un fichier"))} onClose={onClose} className="wide file-picker">
      {listing && !atRoots && listing.roots.length > 1 && (
        <div className="file-roots" role="group" aria-label={t("Emplacements")}>
          {listing.roots.map((r) => (
            <button
              key={r.path}
              type="button"
              disabled={!r.available}
              title={r.available ? r.path : `${r.path} : ${r.reason ?? t("introuvable sur le serveur")}`}
              aria-pressed={here === r.path || here.startsWith(join(r.path, ""))}
              onClick={() => void go(r.path)}
            >
              {r.label}
            </button>
          ))}
        </div>
      )}
      <div className="file-bar">
        <button type="button" onClick={() => void go(listing?.parent ?? "")} disabled={atRoots} aria-label={t("Dossier parent")} title={t("Dossier parent")}>
          ↑
        </button>
        <input
          type="text"
          aria-label={t("Dossier courant")}
          value={address}
          placeholder={t("Choisissez un emplacement ci-dessus")}
          onChange={(e) => setAddress(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void go(address)}
        />
        <input type="search" aria-label={t("Filtrer la liste")} placeholder={t("Filtrer…")} value={filter} onChange={(e) => setFilter(e.target.value)} />
      </div>
      {mode === "read" && !extensions && (
        <label className="inline">
          <input type="checkbox" checked={recognized} onChange={(e) => setRecognized(e.target.checked)} /> {t("Seulement les formats lus par Carriole")}
        </label>
      )}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      <div className="file-list" role="listbox" aria-label={t("Contenu du dossier")} aria-busy={reading}>
        {reading && <Loading text={t("Lecture du dossier…")} overlay />}
        {atRoots &&
          listing?.roots.map((r) => (
            <button key={r.path} type="button" className="file-row dir" disabled={!r.available} onClick={() => void go(r.path)}>
              <span aria-hidden>📁</span>
              <span className="file-name">{r.label}</span>
              <small className={r.available ? "muted" : "error-text"}>{r.available ? r.path : (r.reason ?? t("introuvable sur le serveur"))}</small>
            </button>
          ))}
        {!atRoots &&
          entries.map((e) => (
            <button
              key={e.path}
              type="button"
              role="option"
              aria-selected={selected === e.path}
              className={`file-row ${e.kind}${selected === e.path ? " selected" : ""}`}
              onClick={() => {
                if (e.kind === "dir") return void go(e.path);
                setSelected(e.path);
                if (mode === "write") setName(e.name);
              }}
              onDoubleClick={() => e.kind === "file" && mode === "read" && pick(e.path)}
            >
              <span aria-hidden>{e.kind === "dir" ? "📁" : "📄"}</span>
              <span className="file-name">{e.name}</span>
              <small className="muted">{formatSize(e.size)}</small>
              <small className="muted">{e.kind === "file" ? formatDate(e.modified) : ""}</small>
            </button>
          ))}
        {!atRoots && listing && entries.length === 0 && (
          <p className="muted pad">
            {filter
              ? t("Aucun résultat.")
              : isRoot
                ? t("Emplacement vide. S'il devrait contenir des fichiers, vérifier que le lecteur ou le partage réseau est bien connecté.")
                : recognized
                  ? t("Aucun fichier d'un format lu par Carriole ici : décocher la case ci-dessus pour tout voir.")
                  : t("Dossier vide.")}
          </p>
        )}
        {listing?.truncated && <p className="muted pad">{t("Liste tronquée : affiner avec le filtre ou ouvrir un sous-dossier.")}</p>}
      </div>
      {mode === "write" && (
        <label className="field">
          <span className="field-label">{t("Nom du fichier")}</span>
          <input type="text" value={name} placeholder={t("resultat.gpkg")} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && pick(chosen)} />
        </label>
      )}
      {/* place toujours réservée : la fenêtre ne doit pas changer de taille au premier clic (le double-clic raterait sa cible) */}
      <p className="file-chosen muted">{chosen ?? t("Aucun fichier choisi.")}</p>
      <div className="modal-actions">
        <button onClick={onClose}>{t("Annuler")}</button>
        <button className="primary" disabled={!chosen} onClick={() => pick(chosen)}>
          {t("Choisir")}
        </button>
      </div>
    </Modal>
  );
}
