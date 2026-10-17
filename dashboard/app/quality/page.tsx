import { AsOfBar } from "@/components/asof";
import { Badge, Card, Empty, PageTitle, Stat, Table } from "@/components/ui";
import { getDb } from "@/lib/data";
import { inr, when } from "@/lib/format";
import { asOf, config } from "@/lib/queries";

export const dynamic = "force-dynamic";

interface Quality { checked_at?: string; counts?: Record<string, number>; issues?: Record<string, string[]> }
interface Cost { month?: string; models?: Record<string, { cost_inr: number; calls: number; cache_hit_rate: number | null }> }
interface Size { total_mb?: number; limit_mb?: number; largest?: Record<string, number> }

export default async function QualityPage() {
  const db = await getDb();
  const [freshness, cfg] = await Promise.all([asOf(db, "quality"), config(db)]);
  const q = (cfg.data_quality ?? {}) as Quality;
  const cost = (cfg.ai_cost_report ?? {}) as Cost;
  const size = (cfg.db_size ?? {}) as Size;
  const used = size.total_mb && size.limit_mb ? size.total_mb / size.limit_mb : null;
  return (
    <>
      <PageTitle title="Data quality and capacity" sub="Nightly checks (alerts only when something fails), database size vs the free tier, AI cost and prompt-cache hit rate." />
      <AsOfBar asOf={freshness} now={db.now()} />
      <div className="grid grid-cols-2 gap-3">
        <Stat label="Checks failing" value={Object.keys(q.counts ?? {}).length} sub={q.checked_at ? `checked ${when(q.checked_at)}` : "not run yet"} tone={Object.keys(q.counts ?? {}).length ? "bad" : "good"} />
        <Stat label="Database" value={size.total_mb != null ? `${Math.round(size.total_mb)} MB` : "n/a"} sub={used != null ? `${Math.round(used * 100)}% of ${size.limit_mb} MB` : undefined} tone={used != null && used > 0.7 ? "bad" : undefined} />
      </div>
      <Card title="Failing checks" className="mt-3">
        {!Object.keys(q.issues ?? {}).length && <Empty>All checks passed.</Empty>}
        {Object.entries(q.issues ?? {}).map(([k, v]) => (
          <div key={k} className="border-t border-line py-2.5 first:border-t-0">
            <div className="flex items-center gap-2"><span className="text-sm font-medium">{k.replaceAll("_", " ")}</span><Badge tone="bad">{q.counts?.[k] ?? v.length}</Badge></div>
            {v.slice(0, 3).map((x) => <p key={x} className="mt-1 text-xs text-ink-2">{x}</p>)}
          </div>
        ))}
      </Card>
      <Card title={`AI cost this month${cost.month ? ` (${cost.month.slice(0, 7)})` : ""}`} className="mt-3">
        <Table head={["Model", "Spend", "Calls", "Cache hits"]} rows={Object.entries(cost.models ?? {}).map(([m, v]) => [
          m, inr(v.cost_inr), v.calls, v.cache_hit_rate != null ? `${Math.round(v.cache_hit_rate * 100)}%` : "n/a",
        ])} />
      </Card>
      <Card title="Largest tables" className="mt-3">
        <Table head={["Table", "MB"]} rows={Object.entries(size.largest ?? {}).map(([k, v]) => [k, v])} />
      </Card>
    </>
  );
}
