import { AsOfBar } from "@/components/asof";
import { Badge, Card, PageTitle, Table } from "@/components/ui";
import { getDb } from "@/lib/data";
import { pct, when } from "@/lib/format";
import { asOf, rejected } from "@/lib/queries";

export const dynamic = "force-dynamic";

export default async function Rejected() {
  const db = await getDb();
  const [freshness, rows] = await Promise.all([asOf(db, "rejected"), rejected(db)]);
  const stages = new Map<string, { n: number; resolved: number; wins: number; sum: number }>();
  for (const r of rows) {
    const s = stages.get(r.stage) ?? { n: 0, resolved: 0, wins: 0, sum: 0 };
    s.n++;
    if (r.outcome_status === "resolved" && r.option_return_pct != null) {
      s.resolved++;
      s.sum += r.option_return_pct;
      if (r.option_return_pct > 0) s.wins++;
    }
    stages.set(r.stage, s);
  }
  return (
    <>
      <PageTitle title="Rejected candidates" sub="Every rejection is followed as a counterfactual: would it have won?" />
      <AsOfBar asOf={freshness} now={db.now()} />
      <Card title="By stage">
        <Table head={["Stage", "Rejected", "Resolved", "Would have won", "Avg option"]} rows={[...stages].map(([k, s]) => [
          k, s.n, s.resolved, s.resolved ? `${s.wins}/${s.resolved}` : "-", s.resolved ? pct(s.sum / s.resolved) : "-",
        ])} />
      </Card>
      <Card title="Recent" className="mt-3">
        {rows.map((r) => (
          <div key={r.id} className="border-t border-line py-2.5 first:border-t-0">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="font-medium">{r.symbol}</span>
              {r.direction && <Badge tone={r.direction === "bullish" ? "good" : "bad"}>{r.direction}</Badge>}
              <Badge>{r.stage}</Badge>
              <span className="text-xs text-muted">{when(r.ts)}</span>
            </div>
            <p className="mt-1 text-sm text-ink-2">{r.reason}</p>
            <p className="tnum mt-0.5 text-xs">
              Counterfactual: {r.option_return_pct == null ? "not tracked yet" : `option ${pct(r.option_return_pct)}`}
              {r.outcome_status === "resolved" ? " (resolved)" : " (open)"}
            </p>
          </div>
        ))}
      </Card>
    </>
  );
}
