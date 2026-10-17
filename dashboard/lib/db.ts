/**
 * One small read interface over the database, implemented by Supabase (live, anon key + RLS,
 * owner session) and by in-memory fixtures (DASHBOARD_FIXTURES=1: tests and screenshots).
 * All page logic is written against this interface, so fixtures exercise the same code.
 */
export type Row = Record<string, unknown>;

export interface Query {
  eq?: Record<string, unknown>;
  in?: [string, unknown[]];
  gte?: [string, string | number];
  lt?: [string, string | number];
  notNull?: string;
  order?: [string, "asc" | "desc"];
  limit?: number;
}

export interface Db {
  readonly mode: "live" | "fixtures";
  now(): Date;
  rows<T = Row>(table: string, q?: Query): Promise<T[]>;
  /** Owner switch write (RLS allows only feat.*, brief.*, alerts_muted with a boolean value). */
  setSwitch(key: string, value: boolean): Promise<void>;
  /** Learning-loop decision (RLS: owner only, pending -> accepted / rejected, status only). */
  decideSuggestion(id: number, status: "accepted" | "rejected"): Promise<void>;
  userEmail(): Promise<string | null>;
}

export function applyQuery<T extends Row>(data: T[], q: Query = {}): T[] {
  let out = data.slice();
  if (q.eq) {
    const eq = q.eq;
    out = out.filter((r) => Object.entries(eq).every(([k, v]) => r[k] === v));
  }
  if (q.in) {
    const [col, vals] = q.in;
    out = out.filter((r) => vals.includes(r[col]));
  }
  if (q.gte) {
    const [col, v] = q.gte;
    out = out.filter((r) => r[col] != null && (r[col] as string | number) >= v);
  }
  if (q.lt) {
    const [col, v] = q.lt;
    out = out.filter((r) => r[col] != null && (r[col] as string | number) < v);
  }
  if (q.notNull) {
    const col = q.notNull;
    out = out.filter((r) => r[col] != null);
  }
  if (q.order) {
    const [col, dir] = q.order;
    out.sort((a, b) => {
      const x = a[col] as string | number | null;
      const y = b[col] as string | number | null;
      if (x === y) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      return (x < y ? -1 : 1) * (dir === "asc" ? 1 : -1);
    });
  }
  return q.limit ? out.slice(0, q.limit) : out;
}
