import type { DbKind } from "../api/types";

/** Types de base des connexions : libellé (noms de produits, non traduits) et port par défaut. */
export const DB_KINDS: { kind: DbKind; label: string; port: number }[] = [
  { kind: "postgres", label: "PostgreSQL / PostGIS", port: 5432 },
  { kind: "oracle", label: "Oracle", port: 1521 },
];

export const kindLabel = (kind?: string): string => DB_KINDS.find((k) => k.kind === (kind || "postgres"))?.label ?? String(kind);

export const defaultPort = (kind: DbKind): number => DB_KINDS.find((k) => k.kind === kind)?.port ?? 5432;
