import Link from "next/link";
import { notFound } from "next/navigation";
import { AsOfBar } from "@/components/asof";
import { Bars, LineSeries } from "@/components/charts";
import { Badge, Card, KV, PageTitle, Table } from "@/components/ui";
import { getDb } from "@/lib/data";
import { inr, pct, when } from "@/lib/format";
import { asOf, unrealised } from "@/lib/queries";
import type { Trade, TradeEvent } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function TradePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const db = await getDb();
  const [t] = await db.rows<Trade>("paper_trades", { eq: { id: Number(id) } });
  if (!t) notFound();
  const [freshness, events] = await Promise.all([
    asOf(db, "trades"),
    db.rows<TradeEvent>("trade_events", {
      eq: { trade_id: t.id },
      order: ["ts", "asc"],
    }),
  ]);
  const path = events
    .filter((e) => e.premium != null)
    .map((e) => ({ x: `${e.event} ${when(e.ts)}`, y: e.premium as number }));
  if (t.status === "OPEN" && t.last_premium != null)
    path.push({ x: `now ${when(t.last_ts)}`, y: t.last_premium });
  const pnl = t.status === "OPEN" ? unrealised(t) : (t.pnl ?? 0);
  const attribution = Object.entries(t.attribution_json ?? {}).map(
    ([x, y]) => ({ x, y: Math.round(y) }),
  );
  return (
    <>
      <PageTitle title={`${t.symbol} ${t.strike} ${t.option_type} · #${t.id}`}>
        <div className="mt-2 flex flex-wrap gap-1.5">
          <Badge tone={t.status === "OPEN" ? "info" : "neutral"}>
            {t.status.toLowerCase()}
          </Badge>
          {t.fillability && (
            <Badge
              tone={
                t.fillability === "fillable"
                  ? "good"
                  : t.fillability === "partial"
                    ? "warn"
                    : "bad"
              }
            >
              {t.fillability}
            </Badge>
          )}
          <Link href={`/signals/${t.signal_id}`}>
            <Badge tone="info">Signal S-{t.signal_id}</Badge>
          </Link>
          <Link href={`/stocks/${t.symbol}`}>
            <Badge tone="info">Stock page</Badge>
          </Link>
        </div>
      </PageTitle>
      <AsOfBar asOf={freshness} now={db.now()} />
      <Card title="Position">
        <KV
          items={[
            [
              "P&L",
              <span key="p" className={pnl >= 0 ? "text-good-ink" : "text-bad"}>
                {inr(pnl, true)}
              </span>,
            ],
            ["Lots", `${t.lots} × ${t.lot_size} (${t.booking_mode})`],
            ["Entry", `${t.entry_premium} · ${when(t.entry_ts)}`],
            [
              t.status === "OPEN" ? "Last bid" : "Exit",
              t.status === "OPEN"
                ? `${t.last_premium ?? "n/a"} · ${when(t.last_ts)}`
                : `${t.exit_premium} (${t.exit_reason}) · ${when(t.exit_ts)}`,
            ],
            ["Stop now", String(t.state_json?.stop ?? t.sl)],
            ["Targets", `${t.t1} / ${t.t2} / ${t.t3}`],
            ["Gap/slippage", inr(t.slippage_inr, true)],
            ["Expiry", t.expiry],
          ]}
        />
      </Card>
      {path.length > 1 && (
        <Card title="Premium path" className="mt-3">
          <LineSeries
            data={path}
            label="Premium"
            refs={[
              { y: t.sl, label: "SL" },
              { y: t.t1, label: "T1" },
              { y: t.t2, label: "T2" },
            ]}
          />
        </Card>
      )}
      <Card title="Fills" className="mt-3">
        <Table
          head={["Event", "Time", "Lots", "Fill", "Trigger", "Slippage"]}
          rows={events.map((e) => [
            e.event,
            when(e.ts),
            e.lots ?? "",
            e.premium ?? "",
            e.trigger_level ?? "",
            e.slippage_inr != null ? (
              <span key="s" className={e.gap ? "text-bad" : ""}>
                {inr(e.slippage_inr, true)}
                {e.gap ? " gap" : ""}
              </span>
            ) : (
              ""
            ),
          ])}
        />
      </Card>
      {attribution.length > 0 && (
        <Card title="P&L attribution (₹)" className="mt-3">
          <Bars data={attribution} label="₹" signed />
          <p className="mt-1 text-xs text-ink-2">
            Delta, theta, vega, spread and residual add up to the gross P&L (
            {pct(t.pnl_pct)} of premium).
          </p>
        </Card>
      )}
    </>
  );
}
