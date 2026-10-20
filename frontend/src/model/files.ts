import { formatNumber, t } from "../i18n";

/** « 1 536 » octets → « 1,5 Ko ». */
export function formatSize(bytes: number | null): string {
  if (bytes === null) return "";
  if (bytes < 1024) return t("{{n}} o", { n: bytes });
  const units = [t("Ko"), t("Mo"), t("Go"), t("To")];
  let value = bytes / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i++;
  }
  return `${formatNumber(value, 1)} ${units[i]}`;
}

/** Séparateur du serveur (le serveur de dev sous Windows renvoie des antislashs). */
const sepOf = (path: string) => (path.includes("\\") && !path.includes("/") ? "\\" : "/");

export const join = (dir: string, name: string) => `${dir.replace(/[\\/]+$/, "")}${sepOf(dir)}${name}`;

/** Dossier et nom d'une valeur de champ, motifs (`*`) exclus : là où l'explorateur s'ouvre. */
export function startFolder(value: string): { dir: string; name: string } {
  const clean = value.split("*")[0];
  const cut = Math.max(clean.lastIndexOf("/"), clean.lastIndexOf("\\"));
  if (cut <= 0) return { dir: "", name: "" };
  return { dir: clean.slice(0, cut), name: value.includes("*") ? "" : clean.slice(cut + 1) };
}
