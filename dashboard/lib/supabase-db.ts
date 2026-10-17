import "server-only";
import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import type { Db, Query, Row } from "./db";

export async function supabaseServer() {
  const store = await cookies();
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) throw new Error("NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY are not set");
  return createServerClient(url, key, {
    cookies: {
      getAll: () => store.getAll(),
      setAll: (all) => {
        try {
          all.forEach(({ name, value, options }) => store.set(name, value, options));
        } catch {
          // called from a server component: the proxy refreshes the session instead
        }
      },
    },
  });
}

/** Live data: anon key + the owner's session; row-level security does the access control. */
export class SupabaseDb implements Db {
  readonly mode = "live" as const;

  now(): Date {
    return new Date();
  }

  async rows<T = Row>(table: string, q: Query = {}): Promise<T[]> {
    const sb = await supabaseServer();
    let b = sb.from(table).select("*");
    for (const [k, v] of Object.entries(q.eq ?? {})) b = b.eq(k, v as string);
    if (q.in) b = b.in(q.in[0], q.in[1] as string[]);
    if (q.gte) b = b.gte(q.gte[0], q.gte[1]);
    if (q.lt) b = b.lt(q.lt[0], q.lt[1]);
    if (q.notNull) b = b.not(q.notNull, "is", null);
    if (q.order) b = b.order(q.order[0], { ascending: q.order[1] === "asc", nullsFirst: false });
    b = b.limit(q.limit ?? 1000);
    const { data, error } = await b;
    if (error) throw new Error(`${table}: ${error.message}`);
    return (data ?? []) as T[];
  }

  async setSwitch(key: string, value: boolean): Promise<void> {
    const sb = await supabaseServer();
    const { error } = await sb.from("config").upsert({ key, value_json: value });
    if (error) throw new Error(`could not change ${key}: ${error.message}`);
  }

  async decideSuggestion(id: number, status: "accepted" | "rejected"): Promise<void> {
    const sb = await supabaseServer();
    const { error } = await sb.from("suggestions").update({ status }).eq("id", id).eq("status", "pending");
    if (error) throw new Error(`could not record the decision: ${error.message}`);
  }

  async userEmail(): Promise<string | null> {
    const sb = await supabaseServer();
    const { data } = await sb.auth.getUser();
    return data.user?.email ?? null;
  }
}
