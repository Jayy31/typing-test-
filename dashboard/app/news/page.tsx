import Link from "next/link";
import { AsOfBar } from "@/components/asof";
import { Badge, Card, PageTitle } from "@/components/ui";
import { getDb } from "@/lib/data";
import { when } from "@/lib/format";
import { asOf, evidenceSource } from "@/lib/queries";
import type { NewsItem, NewsTag } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function News({ searchParams }: { searchParams: Promise<{ symbol?: string; sector?: string; type?: string }> }) {
  const f = await searchParams;
  const db = await getDb();
  const [freshness, items, tags] = await Promise.all([
    asOf(db, "news"),
    db.rows<NewsItem>("news_items", { order: ["published_at", "desc"], limit: 200 }),
    db.rows<NewsTag>("news_tags", { order: ["id", "desc"], limit: 600 }),
  ]);
  const byNews = new Map<number, NewsTag[]>();
  tags.forEach((t) => byNews.set(t.news_id, [...(byNews.get(t.news_id) ?? []), t]));
  const shown = items.filter((n) => {
    const ts = byNews.get(n.id) ?? [];
    if (!ts.length) return false;
    return (!f.symbol || ts.some((t) => t.symbol === f.symbol)) && (!f.sector || ts.some((t) => t.sector === f.sector))
      && (!f.type || ts.some((t) => t.event_type === f.type));
  });
  const filters = [f.symbol && `stock ${f.symbol}`, f.sector && `sector ${f.sector}`, f.type && `type ${f.type}`].filter(Boolean);
  return (
    <>
      <PageTitle title="News feed" sub="Tagged news and structured events (filings, deals, results, graph). Tap a tag to filter." />
      {filters.length > 0 && <p className="mb-2 text-sm">Filtered: {filters.join(", ")} · <Link href="/news" className="text-s1">clear</Link></p>}
      <AsOfBar asOf={freshness} now={db.now()} />
      <Card>
        {shown.map((n) => {
          const src = evidenceSource(n.source);
          return (
            <div key={n.id} className="border-t border-line py-2.5 first:border-t-0">
              <div className="flex flex-wrap items-center gap-1.5">
                <Badge tone={src.tone}>{src.label}</Badge>
                <span className="text-xs text-muted">{when(n.published_at)}</span>
              </div>
              <a href={n.url} target="_blank" rel="noreferrer" className="mt-1 block text-sm">{n.title}</a>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {(byNews.get(n.id) ?? []).map((t) => (
                  <span key={t.id} className="flex gap-1">
                    {t.symbol && <Link href={`/news?symbol=${t.symbol}`}><Badge tone="info">{t.symbol}</Badge></Link>}
                    {!t.symbol && t.sector && <Link href={`/news?sector=${t.sector}`}><Badge>{t.sector}</Badge></Link>}
                    {t.event_type && <Link href={`/news?type=${encodeURIComponent(t.event_type)}`}><Badge>{t.event_type}</Badge></Link>}
                    {t.sentiment != null && <Badge tone={t.sentiment > 0 ? "good" : "bad"}>{t.sentiment > 0 ? "+" : ""}{t.sentiment.toFixed(2)}</Badge>}
                  </span>
                ))}
              </div>
            </div>
          );
        })}
      </Card>
    </>
  );
}
