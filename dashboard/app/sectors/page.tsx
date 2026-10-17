import Link from "next/link";
import { AsOfBar } from "@/components/asof";
import { Card, PageTitle } from "@/components/ui";
import { getDb } from "@/lib/data";
import { asOf, LIVE } from "@/lib/queries";
import type { NewsItem, NewsTag, Signal } from "@/lib/types";

export const dynamic = "force-dynamic";

/** Diverging fill (blue = positive, orange-red = negative, gray midpoint), text stays in ink. */
function fill(v: number): string {
  const a = Math.min(1, Math.abs(v)) * 0.55 + 0.08;
  return v >= 0 ? `color-mix(in oklab, var(--s1) ${a * 100}%, var(--surface))` : `color-mix(in oklab, var(--s2) ${a * 100}%, var(--surface))`;
}

export default async function Sectors() {
  const db = await getDb();
  const now = db.now();
  const since = new Date(now.getTime() - 48 * 3600_000).toISOString();
  const [freshness, news, tags, sigs] = await Promise.all([
    asOf(db, "sectors"),
    db.rows<NewsItem>("news_items", { gte: ["published_at", since] }),
    db.rows<NewsTag>("news_tags", { order: ["id", "desc"], limit: 2000 }),
    db.rows<Signal>("signals", { in: ["status", LIVE] }),
  ]);
  const recent = new Set(news.map((n) => n.id));
  const agg = new Map<string, { sum: number; n: number }>();
  for (const t of tags) {
    if (!recent.has(t.news_id) || !t.sector || t.sentiment == null) continue;
    const a = agg.get(t.sector) ?? { sum: 0, n: 0 };
    a.sum += t.sentiment;
    a.n++;
    agg.set(t.sector, a);
  }
  const rows = [...agg].map(([sector, a]) => ({ sector, avg: a.sum / a.n, n: a.n, signals: sigs.filter((s) => s.sector === sector).length }))
    .sort((a, b) => b.avg - a.avg);
  return (
    <>
      <PageTitle title="Sector heatmap" sub="Average sentiment of tagged items in the last 48 h; blue positive, orange negative." />
      <AsOfBar asOf={freshness} now={now} />
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {rows.map((r) => (
          <Link key={r.sector} href={`/news?sector=${encodeURIComponent(r.sector)}`} className="rounded-xl border border-line p-3" style={{ background: fill(r.avg) }}>
            <div className="text-sm font-semibold text-ink">{r.sector}</div>
            <div className="tnum mt-1 text-lg text-ink">{r.avg > 0 ? "+" : ""}{r.avg.toFixed(2)}</div>
            <div className="text-xs text-ink-2">{r.n} item(s) · {r.signals} live signal(s)</div>
          </Link>
        ))}
      </div>
      {rows.length === 0 && <Card><p className="text-sm text-muted">No tagged sector news in the last 48 h.</p></Card>}
    </>
  );
}
