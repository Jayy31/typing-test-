import { notFound } from "next/navigation";
import { Badge, Card, PageTitle, Stat } from "@/components/ui";
import { getDb } from "@/lib/data";
import { inr, pct } from "@/lib/format";

export const dynamic = "force-dynamic";

interface Perf { n?: number; win_rate?: number; expectancy_pct?: number; ci?: [number, number]; pnl_inr?: number }
interface Ranked { name: string; n: number; expectancy_pct: number }
interface Report {
  id: number; month: string; text: string; ai_summary: string | null; ai_summary_status: string | null;
  data_json: { trades?: { all?: Perf; excluding_unfillable?: Perf }; worked?: { trigger_types?: Ranked[]; sectors?: Ranked[] };
    did_not_work?: { trigger_types?: Ranked[]; sectors?: Ranked[] }; ai_spend_inr?: number; ai_budget_inr?: number; risk?: { max_drawdown_pct?: number } };
}

function List({ items }: { items?: Ranked[] }) {
  if (!items?.length) return <p className="text-sm text-muted">None with enough trades.</p>;
  return <>{items.map((x) => <p key={x.name} className="tnum flex justify-between text-sm"><span>{x.name} <span className="text-xs text-ink-2">n={x.n}</span></span><span>{pct(x.expectancy_pct)}</span></p>)}</>;
}

export default async function ReportPage({ params }: { params: Promise<{ month: string }> }) {
  const { month } = await params;
  const db = await getDb();
  const [r] = await db.rows<Report>("monthly_reports", { eq: { month: `${month}-01` } });
  if (!r) notFound();
  const d = r.data_json;
  const a = d.trades?.all ?? {};
  const f = d.trades?.excluding_unfillable ?? {};
  return (
    <>
      <PageTitle title={`Monthly report ${month}`} sub="Paper trading. Figures only from stored data." />
      {r.ai_summary && (
        <Card title="Summary" action={<Badge tone="good">every figure checked against the report</Badge>}>
          <p className="text-sm leading-relaxed">{r.ai_summary}</p>
        </Card>
      )}
      {!r.ai_summary && <p className="mb-3 rounded-lg bg-grid/60 px-3 py-2 text-xs text-ink-2">No summary paragraph ({r.ai_summary_status ?? "off"}): figures only.</p>}
      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Trades" value={a.n ?? 0} sub={`win rate ${a.win_rate != null ? Math.round(a.win_rate * 100) : "n/a"}%`} />
        <Stat label="Expectancy" value={pct(a.expectancy_pct)} sub={a.ci ? `90%: ${pct(a.ci[0])} … ${pct(a.ci[1])}` : undefined} tone={(a.expectancy_pct ?? 0) >= 0 ? "good" : "bad"} />
        <Stat label="Excl. unfillable" value={pct(f.expectancy_pct)} sub={`${f.n ?? 0} trades`} />
        <Stat label="P&L" value={inr(a.pnl_inr, true)} sub={`max DD ${d.risk?.max_drawdown_pct ?? "n/a"}%`} />
      </div>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <Card title="What worked"><p className="mb-1 text-xs text-muted">Trigger types</p><List items={d.worked?.trigger_types} /><p className="mt-2 mb-1 text-xs text-muted">Sectors</p><List items={d.worked?.sectors} /></Card>
        <Card title="What did not"><p className="mb-1 text-xs text-muted">Trigger types</p><List items={d.did_not_work?.trigger_types} /><p className="mt-2 mb-1 text-xs text-muted">Sectors</p><List items={d.did_not_work?.sectors} /></Card>
      </div>
      <Card title="Full report" className="mt-3">
        <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed">{r.text}</pre>
      </Card>
    </>
  );
}
