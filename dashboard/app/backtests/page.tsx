import { AsOfBar } from "@/components/asof";
import { LineSeries } from "@/components/charts";
import { Card, PageTitle, Table } from "@/components/ui";
import { getDb } from "@/lib/data";
import { day, pct } from "@/lib/format";
import { asOf } from "@/lib/queries";

export const dynamic = "force-dynamic";

interface Run { id: number; period_start: string; period_end: string; finished_at: string | null; notes: string | null;
  metrics_json: { variant?: { name?: string }; all?: { n?: number; expectancy_pct?: number; win_rate?: number; ci90?: [number, number] }; risk?: { max_drawdown_pct?: number }; equity?: number[]; r1_note?: string } | null }

export default async function Backtests() {
  const db = await getDb();
  const [freshness, runs] = await Promise.all([asOf(db, "backtests"), db.rows<Run>("backtest_runs", { order: ["id", "desc"], limit: 40 })]);
  const best = runs.find((r) => r.metrics_json?.equity?.length);
  const verdict = runs.find((r) => r.notes?.startsWith("VERDICT"));
  return (
    <>
      <PageTitle title="Backtests" sub="Walk-forward 12/3 months, costs and spreads, no look-ahead; fixed vs v2.1 rules, graph variants, benchmarks." />
      <AsOfBar asOf={freshness} now={db.now()} />
      <p className="mb-3 rounded-lg bg-grid/60 px-3 py-2 text-xs text-ink-2">
        R1: AI replay uses only predictions made after AI_MODEL_KNOWLEDGE_CUTOFF; earlier history is used for AI labels only, never predictions.
      </p>
      {verdict && <Card title="Verdict" className="mb-3"><p className="text-sm">{verdict.notes}</p></Card>}
      <Card title="Variants">
        <Table head={["Variant", "Trades", "Expectancy", "90% CI", "Win", "Max DD"]} rows={runs.map((r) => {
          const m = r.metrics_json ?? {};
          return [m.variant?.name ?? `run ${r.id}`, m.all?.n ?? "", pct(m.all?.expectancy_pct),
            m.all?.ci90 ? `${pct(m.all.ci90[0])} … ${pct(m.all.ci90[1])}` : "n/a",
            m.all?.win_rate != null ? `${Math.round(m.all.win_rate * 100)}%` : "", pct(m.risk?.max_drawdown_pct)];
        })} />
        {runs[0] && <p className="mt-2 text-xs text-ink-2">Period {day(runs[0].period_start)} to {day(runs[0].period_end)} · % of premium is primary; ₹ before Jul-2024 is approximate (inferred lot sizes).</p>}
      </Card>
      {best && (
        <Card title={`Equity (index): ${best.metrics_json?.variant?.name}`} className="mt-3">
          <LineSeries data={(best.metrics_json?.equity ?? []).map((y, i) => ({ x: `${i + 1}`, y: +y.toFixed(1) }))} label="Equity index" />
        </Card>
      )}
    </>
  );
}
