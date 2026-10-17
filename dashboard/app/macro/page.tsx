import { AsOfBar } from "@/components/asof";
import { LineSeries } from "@/components/charts";
import { Card, PageTitle, Stat, Table } from "@/components/ui";
import { getDb } from "@/lib/data";
import { pct, when } from "@/lib/format";
import { asOf } from "@/lib/queries";

export const dynamic = "force-dynamic";

interface Snap { ts: string; brent: number | null; usdinr: number | null; dxy: number | null; us10y: number | null; india_vix: number | null;
  fii_net: number | null; dii_net: number | null; adr_json: Record<string, { adr: string; change_pct: number }> | null; commodities_json: Record<string, number> | null }

export default async function Macro() {
  const db = await getDb();
  const [freshness, snaps] = await Promise.all([asOf(db, "macro"), db.rows<Snap>("macro_snapshots", { order: ["ts", "asc"], limit: 200 })]);
  const last = snaps.at(-1);
  const first = snaps[0];
  const chg = (k: keyof Snap) => (last && first && last[k] != null && first[k] ? ((Number(last[k]) / Number(first[k]) - 1) * 100) : null);
  const flows = [...snaps].reverse().find((s) => s.fii_net != null);
  return (
    <>
      <PageTitle title="Macro" />
      <AsOfBar asOf={freshness} now={db.now()} />
      {last && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <Stat label="Brent" value={last.brent?.toFixed(2) ?? "n/a"} sub={pct(chg("brent"))} />
          <Stat label="USD/INR" value={last.usdinr?.toFixed(2) ?? "n/a"} sub={pct(chg("usdinr"), true, 2)} />
          <Stat label="India VIX" value={last.india_vix?.toFixed(2) ?? "n/a"} sub={pct(chg("india_vix"))} />
          <Stat label="DXY" value={last.dxy?.toFixed(2) ?? "n/a"} />
          <Stat label="US 10Y" value={last.us10y != null ? `${last.us10y.toFixed(2)}%` : "n/a"} />
          <Stat label="FII / DII (₹ cr)" value={flows ? `${Math.round(flows.fii_net ?? 0)} / ${Math.round(flows.dii_net ?? 0)}` : "n/a"} sub={flows ? `final for ${when(flows.ts)}` : "pending"} />
        </div>
      )}
      <Card title="Brent (window shown)" className="mt-3">
        <LineSeries data={snaps.map((s) => ({ x: when(s.ts).slice(-5), y: s.brent ?? 0 }))} label="Brent" height={150} />
      </Card>
      <Card title="Commodities" className="mt-3">
        <Table head={["Name", "Last", "Change"]} rows={Object.entries(last?.commodities_json ?? {}).map(([k, v]) => {
          const old = first?.commodities_json?.[k];
          return [k, v.toLocaleString("en-IN"), old ? pct((v / old - 1) * 100) : "n/a"];
        })} />
      </Card>
      <Card title="Indian ADRs" className="mt-3">
        <Table head={["Stock", "ADR", "Change"]} rows={Object.entries(last?.adr_json ?? {}).map(([k, v]) => [k, v.adr, pct(v.change_pct)])} />
      </Card>
    </>
  );
}
