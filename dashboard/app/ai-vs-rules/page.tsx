import { AsOfBar } from "@/components/asof";
import { Badge, Card, PageTitle, Stat, Table } from "@/components/ui";
import { getDb } from "@/lib/data";
import { pct, predictor, when } from "@/lib/format";
import { asOf } from "@/lib/queries";

export const dynamic = "force-dynamic";

interface Row { id: number; analysis_id: number; symbol: string; created_at: string; ai_direction: string | null; ai_signal: boolean; rules_direction: string | null; rules_reason: string | null; realised_move_pct: number | null }

function side(rows: Row[], pick: (r: Row) => string | null) {
  const moves = rows.flatMap((r) => { const d = pick(r); return d && r.realised_move_pct != null ? [r.realised_move_pct * (d === "bullish" ? 1 : -1)] : []; });
  return moves.length ? { n: moves.length, hit: moves.filter((m) => m > 0).length / moves.length, avg: moves.reduce((a, b) => a + b, 0) / moves.length } : null;
}

export default async function AiVsRules() {
  const db = await getDb();
  const [freshness, rows] = await Promise.all([asOf(db, "aivsrules"), db.rows<Row>("ai_vs_rules", { order: ["created_at", "desc"], limit: 500 })]);
  const ans = await db.rows<{ id: number; prediction_source: string | null; ai_preset: string | null }>("analyses", { in: ["id", rows.map((r) => r.analysis_id)] });
  const src = new Map(ans.map((a) => [a.id, a]));
  const groups = new Map<string, Row[]>();
  rows.forEach((r) => {
    const a = src.get(r.analysis_id);
    const k = `${predictor(a?.prediction_source)}${a?.ai_preset ? ` · ${a.ai_preset}` : ""}`;
    groups.set(k, [...(groups.get(k) ?? []), r]);
  });
  const sides: [string, ReturnType<typeof side>][] = [
    ["AI signals", side(rows, (r) => (r.ai_signal ? r.ai_direction : null))],
    ["Rules (analogues only)", side(rows, (r) => r.rules_direction)],
    ["AI only", side(rows, (r) => (r.ai_signal && !r.rules_direction ? r.ai_direction : null))],
    ["Rules only", side(rows, (r) => (r.rules_direction && !r.ai_signal ? r.rules_direction : null))],
  ];
  return (
    <>
      <PageTitle title="AI vs rules" sub="For every analysis: what the AI did and what the rules-only path (news direction confirmed by event-study analogues) would have done, scored on the stock's realised move." />
      <AsOfBar asOf={freshness} now={db.now()} />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {sides.map(([label, s]) => <Stat key={label} label={label} value={s ? `${Math.round(s.hit * 100)}% hit` : "n/a"} sub={s ? `${s.n} calls · avg ${pct(s.avg)}` : "nothing resolved"} />)}
      </div>
      <Card title="AI signals by prediction source" className="mt-3">
        <Table head={["Source", "AI calls resolved", "Hit", "Avg move"]} rows={[...groups].map(([k, rs]) => {
          const s = side(rs, (r) => (r.ai_signal ? r.ai_direction : null));
          return [k, s?.n ?? 0, s ? `${Math.round(s.hit * 100)}%` : "n/a", s ? pct(s.avg) : "n/a"];
        })} />
      </Card>
      <Card title="Recent" className="mt-3">
        <Table head={["When", "Stock", "AI", "Rules", "Move"]} rows={rows.slice(0, 25).map((r) => [
          when(r.created_at), r.symbol,
          r.ai_signal ? <Badge key="a" tone={r.ai_direction === "bullish" ? "good" : "bad"}>{r.ai_direction}</Badge> : <span key="a" className="text-xs text-muted">no signal</span>,
          r.rules_direction ? <Badge key="r" tone="info">{r.rules_direction}</Badge> : <span key="r" className="text-xs text-muted">{r.rules_reason}</span>,
          r.realised_move_pct == null ? "pending" : pct(r.realised_move_pct),
        ])} />
      </Card>
    </>
  );
}
