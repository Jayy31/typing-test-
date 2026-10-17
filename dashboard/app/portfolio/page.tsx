import { AsOfBar } from "@/components/asof";
import { LineSeries } from "@/components/charts";
import { Card, PageTitle, Stat, Table } from "@/components/ui";
import { getDb } from "@/lib/data";
import { day, inr, pct } from "@/lib/format";
import { asOf } from "@/lib/queries";
import type { Snapshot } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function Portfolio() {
  const db = await getDb();
  const [freshness, snaps] = await Promise.all([asOf(db, "portfolio"), db.rows<Snapshot>("portfolio_snapshots", { order: ["ts", "asc"], limit: 400 })]);
  const last = snaps.at(-1);
  return (
    <>
      <PageTitle title="Portfolio" sub="Notional capital, paper trades only." />
      <AsOfBar asOf={freshness} now={db.now()} />
      {last && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label="Equity" value={inr(last.equity)} sub={`capital ${inr(last.capital)}`} />
          <Stat label="Drawdown" value={pct(-last.drawdown_pct)} tone={last.drawdown_pct > 5 ? "bad" : undefined} />
          <Stat label="Capital used" value={inr(last.capital_used_inr)} sub={`${last.open_trades} open`} />
          <Stat label="Premium at risk" value={inr(last.premium_at_risk_inr)} />
        </div>
      )}
      <Card title="Equity" className="mt-3">
        <LineSeries data={snaps.map((s) => ({ x: day(s.ts).slice(0, 6), y: Math.round(s.equity) }))} label="Equity" unit="" />
      </Card>
      <Card title="Drawdown" className="mt-3">
        <LineSeries data={snaps.map((s) => ({ x: day(s.ts).slice(0, 6), y: -+s.drawdown_pct.toFixed(2) }))} label="Drawdown" unit="%" height={140} />
      </Card>
      <Card title="Exposure by theme" className="mt-3">
        <Table head={["Theme", "Premium"]} rows={Object.entries(last?.exposure_json ?? {}).map(([k, v]) => [k, inr(v)])} />
      </Card>
    </>
  );
}
