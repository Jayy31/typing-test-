import "server-only";
import type { Db } from "./db";

export function fixturesMode(): boolean {
  return process.env.DASHBOARD_FIXTURES === "1";
}

let fixtureDb: Db | null = null;

export async function getDb(): Promise<Db> {
  if (fixturesMode()) {
    if (!fixtureDb) {
      const { FixtureDb } = await import("./fixtures");
      fixtureDb = new FixtureDb();
    }
    return fixtureDb;
  }
  const { SupabaseDb } = await import("./supabase-db");
  return new SupabaseDb();
}
