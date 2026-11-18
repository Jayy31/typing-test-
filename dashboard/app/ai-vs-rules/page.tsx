import { AsOfBar } from "@/components/asof";
import { Badge, Card, PageTitle, Stat, Table } from "@/components/ui";
import { getDb } from "@/lib/data";
import { pct, predictor, when } from "@/lib/format";
import { asOf } from "@/lib/queries";

export const dynamic = "force-dynamic";

interface Row { id: number; analysis_id: number; symbol: string; created_at: string; ai_direction: string | null; ai_signal: boolean; rules_direction: string | null; rules_reason: string | null; realised_move_pct: number | null }

// One call per news event (cluster) + stock + direction: several articles on one story count once.
function side(rows: Row[], pick: (r: Row) => string | null, event: (r: Row) => string = (r) => `a${r.analysis_id}`) {
  const g = new Map<string, number[]>();
  rows.forEach((r) => {
    const d = pick(r);
    if (!d || r.realised_move_pct == null) return;
    const k = `${event(r)}|${r.symbol}|${d}`;
    g.set(k, [...(g.get(k) ?? []), r.realised_move_pct * (d === "bullish" ? 1 : -1)]);
  });
  const moves = [...g.values()].map((v) => v.reduce((a, b) => a + b, 0) / v.length);
  return moves.length ? { n: moves.length, hit: moves.filter((m) => m > 0).length / moves.length, avg: moves.reduce((a, b) => a + b, 0) / moves.length } : null;
}

export default async function AiVsRules() {
  const db = await getDb();
  const [freshness, rows] = await Promise.all([asOf(db, "aivsrules"), db.rows<Row>("ai_vs_rules", { order: ["created_at", "desc"], limit: 500 })]);
  const ans = await db.rows<{ id: number; cluster_id: number | null; prediction_source: string | null; ai_preset: string | null }>("analyses", { in: ["id", rows.map((r) => r.analysis_id)] });
  const src = new Map(ans.map((a) => [a.id, a]));
  const ev = (r: Row) => { const c = src.get(r.analysis_id)?.cluster_id; return c != null ? `c${c}` : `a${r.analysis_id}`; };
  const groups = new Map<string, Row[]>();
  rows.forEach((r) => {
    const a = src.get(r.analysis_id);
    const k = `${predictor(a?.prediction_source)}${a?.ai_preset ? ` · ${a.ai_preset}` : ""}`;
    groups.set(k, [...(groups.get(k) ?? []), r]);
  });
  const sides: [string, ReturnType<typeof side>][] = [
    ["AI signals", side(rows, (r) => (r.ai_signal ? r.ai_direction : null), ev)],
    ["Rules (analogues only)", side(rows, (r) => r.rules_direction, ev)],
    ["AI only", side(rows, (r) => (r.ai_signal && !r.rules_direction ? r.ai_direction : null), ev)],
    ["Rules only", side(rows, (r) => (r.rules_direction && !r.ai_signal ? r.rules_direction : null), ev)],
  ];
  return (
    <>
      <PageTitle title="AI vs rules" sub="For every analysis: what the AI did and what the rules-only path (news direction confirmed by event-study analogues) would have done, scored on the stock's realised move. One call per news event; analyses with no AI direction are scored from daily closes." />
      <AsOfBar asOf={freshness} now={db.now()} />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {sides.map(([label, s]) => <Stat key={label} label={label} value={s ? `${Math.round(s.hit * 100)}% hit` : "n/a"} sub={s ? `${s.n} events · avg ${pct(s.avg)}` : "nothing resolved"} />)}
      </div>
      <Card title="AI signals by prediction source" className="mt-3">
        <Table head={["Source", "AI calls resolved", "Hit", "Avg move"]} rows={[...groups].map(([k, rs]) => {
          const s = side(rs, (r) => (r.ai_signal ? r.ai_direction : null), ev);
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
