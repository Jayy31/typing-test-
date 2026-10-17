import "server-only";
import type { Db } from "./db";
import { freshness, PAGE_JOBS, worst, type Freshness } from "./fresh";
import type { ConfigRow, JobRun, KgPath, NewsItem, NewsTag, Rejected, Signal, Trade } from "./types";

export const LIVE = ["NEW", "ACTIVE", "UPGRADED", "DOWNGRADED"];

export function istDayStart(now: Date): string {
  const ist = new Date(now.getTime() + 330 * 60_000);
  ist.setUTCHours(0, 0, 0, 0);
  return new Date(ist.getTime() - 330 * 60_000).toISOString();
}

export interface AsOf {
  ts: string | null;
  level: Freshness;
  jobs: { job: string; last: string | null; level: Freshness }[];
}

export async function asOf(db: Db, page: string): Promise<AsOf> {
  const specs = PAGE_JOBS[page] ?? [];
  const now = db.now();
  const jobs = await Promise.all(
    specs.map(async (spec) => {
      const [last] = await db.rows<JobRun>("job_runs", { eq: { job: spec.job, status: "ok" }, order: ["finished_at", "desc"], limit: 1 });
      const ts = last?.finished_at ?? null;
      return { job: spec.job, last: ts, level: freshness(ts ? new Date(ts) : null, spec, now) };
    }),
  );
  const times = jobs.map((j) => j.last).filter((t): t is string => !!t).sort();
  return { ts: times[0] ?? null, level: worst(jobs.map((j) => j.level)), jobs };
}

export async function config(db: Db): Promise<Record<string, unknown>> {
  const rows = await db.rows<ConfigRow>("config");
  return Object.fromEntries(rows.map((r) => [r.key, r.value_json]));
}

export function unrealised(t: Trade): number {
  const left = 1 - (t.state_json?.fills ?? []).reduce((a, f) => a + (f.fraction ?? 0), 0);
  return ((t.last_premium ?? t.entry_premium) - t.entry_premium) * t.lot_size * t.lots * left;
}

export async function liveSignals(db: Db): Promise<Signal[]> {
  return db.rows<Signal>("signals", { in: ["status", LIVE], order: ["score", "desc"] });
}

export async function trades(db: Db, status?: "OPEN" | "CLOSED"): Promise<Trade[]> {
  return db.rows<Trade>("paper_trades", { ...(status ? { eq: { status } } : {}), order: ["entry_ts", "desc"] });
}

export type Health = "green" | "amber" | "red";

export async function systemHealth(db: Db): Promise<{ level: Health; problems: string[]; aiToday: number; aiDailyCap: number | null }> {
  const now = db.now();
  const cfg = await config(db);
  const problems: string[] = [];
  const health = (cfg.source_health ?? {}) as Record<string, { consecutive_failures?: number; last_error?: string }>;
  for (const [name, h] of Object.entries(health)) {
    if ((h.consecutive_failures ?? 0) >= 3) problems.push(`${name}: ${h.consecutive_failures} failures in a row (${h.last_error ?? "error"})`);
  }
  const page = await Promise.all(Object.keys(PAGE_JOBS).map((p) => asOf(db, p)));
  const red = new Set(page.flatMap((p) => p.jobs.filter((j) => j.level === "red").map((j) => j.job)));
  red.forEach((j) => problems.push(`${j}: no successful run for too long`));
  const usage = await db.rows<{ cost_inr: number }>("ai_usage", { gte: ["ts", istDayStart(now)] });
  const aiToday = usage.reduce((a, u) => a + (u.cost_inr ?? 0), 0);
  const amber = page.some((p) => p.level === "amber") || problems.length > 0;
  return { level: red.size ? "red" : amber ? "amber" : "green", problems, aiToday, aiDailyCap: null };
}

export interface ReviewItem { kind: string; text: string; href: string }

export async function reviewItems(db: Db): Promise<ReviewItem[]> {
  const regs = await db.rows<{ id: number; regulator: string; title: string; symbol: string | null; match_confidence: number | null }>(
    "regulatory_actions", { order: ["disclosed_at", "desc"], limit: 50 },
  );
  const items: ReviewItem[] = regs
    .filter((r) => !r.symbol && (r.match_confidence ?? 0) > 0)
    .map((r) => ({ kind: "Regulator match", text: `${r.regulator.toUpperCase()}: ${r.title} (match ${(r.match_confidence ?? 0).toFixed(2)})`, href: "/digest" }));
  const cfg = await config(db);
  for (const u of (cfg.unverified_alerts ?? []) as { item: string }[]) items.push({ kind: "Unverified", text: u.item, href: "/health" });
  return items;
}

export async function evidenceFor(db: Db, ids: number[], symbol: string) {
  const news = await db.rows<NewsItem>("news_items", { in: ["id", ids] });
  const tags = await db.rows<NewsTag>("news_tags", { in: ["news_id", ids] });
  return news
    .map((n) => ({ news: n, tag: tags.find((t) => t.news_id === n.id && t.symbol === symbol) ?? tags.find((t) => t.news_id === n.id) }))
    .sort((a, b) => (b.news.published_at ?? "").localeCompare(a.news.published_at ?? ""));
}

export function evidenceSource(source: string): { label: string; tone: "good" | "warn" | "info" | "neutral" } {
  if (source.startsWith("kg:")) {
    const basis = source.split(":")[3];
    return basis === "base_rate" ? { label: "graph · event-study base rate", tone: "good" } : { label: "graph · default sign (low weight)", tone: "warn" };
  }
  if (source.startsWith("deep:")) {
    return source.endsWith(":base_rate") ? { label: "filing · event-study base rate", tone: "good" } : { label: "filing · hand-set rule (low weight)", tone: "warn" };
  }
  if (source === "nse_announcements") return { label: "official filing", tone: "info" };
  return { label: source, tone: "neutral" };
}

export async function kgPathsFor(db: Db, symbol: string): Promise<KgPath[]> {
  return db.rows<KgPath>("kg_paths", { eq: { target: symbol }, order: ["created_at", "desc"], limit: 20 });
}

export async function rejected(db: Db, limit = 100): Promise<Rejected[]> {
  return db.rows<Rejected>("rejected_candidates", { order: ["ts", "desc"], limit });
}
