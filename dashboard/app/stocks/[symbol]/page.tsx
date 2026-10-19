import Link from "next/link";
import { notFound } from "next/navigation";
import { AsOfBar } from "@/components/asof";
import { featureLabel } from "@/components/positioning";
import {
  Badge,
  Card,
  Direction,
  Empty,
  KV,
  PageTitle,
  Row,
} from "@/components/ui";
import { getDb } from "@/lib/data";
import { pct, when } from "@/lib/format";
import { asOf } from "@/lib/queries";
import type { Signal, Trade } from "@/lib/types";

export const dynamic = "force-dynamic";

type Snapshot = {
  day: string;
  symbol: string;
  source: string;
  states_json: Record<string, [string, number]>;
};

export default async function StockPage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol: raw } = await params;
  const symbol = decodeURIComponent(raw).toUpperCase();
  const db = await getDb();
  const [inst] = await db.rows<{
    symbol: string;
    name: string | null;
    sector: string | null;
    sub_sector: string | null;
    lot_size: number | null;
    is_fno: boolean;
  }>("instruments", { eq: { symbol } });
  if (!inst) notFound();
  const [freshness, snaps, sigs, trades, tags, [regime]] = await Promise.all([
    asOf(db, "stocks"),
    db.rows<Snapshot>("positioning_snapshots", {
      eq: { symbol },
      order: ["day", "desc"],
      limit: 5,
    }),
    db.rows<Signal>("signals", {
      eq: { symbol },
      order: ["created_at", "desc"],
      limit: 10,
    }),
    db.rows<Trade>("paper_trades", {
      eq: { symbol },
      order: ["entry_ts", "desc"],
      limit: 10,
    }),
    db.rows<{
      news_id: number;
      event_type: string | null;
      sentiment: number | null;
    }>("news_tags", { eq: { symbol }, limit: 20 }),
    db.rows<{ day: string; label: string; inputs: string }>("market_regime", {
      order: ["day", "desc"],
      limit: 1,
    }),
  ]);
  const newsIds = [...new Set(tags.map((t) => t.news_id))];
  const news = newsIds.length
    ? (
        await db.rows<{
          id: number;
          title: string;
          published_at: string;
          url: string;
        }>("news_items", { order: ["published_at", "desc"] })
      )
        .filter((n) => newsIds.includes(n.id))
        .slice(0, 8)
    : [];
  const latest = snaps[0];
  return (
    <>
      <PageTitle
        title={`${inst.symbol} · ${inst.name ?? ""}`}
        sub={`${inst.sector ?? "?"} · ${inst.sub_sector ?? ""} · lot ${inst.lot_size ?? "?"}`}
      >
        <div className="mt-2 flex flex-wrap gap-1.5">
          <Badge tone={inst.is_fno ? "good" : "bad"}>
            {inst.is_fno ? "F&O stock" : "not in F&O"}
          </Badge>
          {regime && <Badge>Market regime {regime.label}</Badge>}
          <Link href={`/news?symbol=${symbol}`}>
            <Badge tone="info">All news</Badge>
          </Link>
          <Link href={`/graph?symbol=${symbol}`}>
            <Badge tone="info">Graph</Badge>
          </Link>
        </div>
      </PageTitle>
      <AsOfBar asOf={freshness} now={db.now()} />

      <Card
        title="Option-market positioning"
        action={
          latest ? (
            <span className="text-xs text-muted">
              {latest.day} · {latest.source === "eod" ? "end of day" : "live"}
            </span>
          ) : undefined
        }
      >
        {!latest && (
          <Empty>
            No positioning data yet (needs the futures/options history or a
            connected broker).
          </Empty>
        )}
        {latest && (
          <KV
            items={Object.entries(latest.states_json).map(
              ([f, [state, value]]) => [
                featureLabel(f),
                <span key={f}>
                  {state.replaceAll("_", " ")}{" "}
                  <span className="text-xs text-muted">({value})</span>
                </span>,
              ],
            )}
          />
        )}
        {snaps.length > 1 && (
          <p className="mt-2 text-xs text-ink-2">
            OI build-up, last days:{" "}
            {snaps
              .map(
                (x) =>
                  `${x.day.slice(5)} ${(x.states_json.oi_buildup?.[0] ?? "n/a").replaceAll("_", " ")}`,
              )
              .join(" · ")}
          </p>
        )}
        <p className="mt-2 text-xs text-muted">
          Evidence only: positioning never creates a signal on its own.
        </p>
      </Card>

      <Card title="Signals" className="mt-3">
        {sigs.length === 0 && <Empty>No signals on this stock.</Empty>}
        {sigs.map((s) => (
          <Row key={s.id} href={`/signals/${s.id}`}>
            <div className="flex items-center gap-2">
              <span>S-{s.id}</span>
              <Direction value={s.direction} />
            </div>
            <span className="text-xs text-ink-2">
              {s.status.toLowerCase()} · score {Math.round(s.score)} ·{" "}
              {when(s.created_at)}
            </span>
          </Row>
        ))}
      </Card>

      <Card title="Paper trades" className="mt-3">
        {trades.length === 0 && <Empty>No trades on this stock.</Empty>}
        {trades.map((t) => (
          <Row key={t.id} href={`/trades/${t.id}`}>
            <span>
              {t.strike} {t.option_type} · {t.status.toLowerCase()}
            </span>
            <span
              className={
                (t.pnl_pct ?? 0) >= 0 ? "tnum text-good-ink" : "tnum text-bad"
              }
            >
              {t.pnl_pct != null ? pct(t.pnl_pct) : "open"}
            </span>
          </Row>
        ))}
      </Card>

      <Card title="Recent news" className="mt-3">
        {news.length === 0 && <Empty>No tagged news.</Empty>}
        {news.map((n) => (
          <div
            key={n.id}
            className="border-t border-line py-2 first:border-t-0"
          >
            <a
              href={n.url}
              target="_blank"
              rel="noreferrer"
              className="text-sm"
            >
              {n.title}
            </a>
            <div className="text-xs text-muted">{when(n.published_at)}</div>
          </div>
        ))}
      </Card>
    </>
  );
}
