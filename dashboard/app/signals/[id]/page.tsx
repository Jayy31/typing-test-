import Link from "next/link";
import { notFound } from "next/navigation";
import { AsOfBar } from "@/components/asof";
import { Bars } from "@/components/charts";
import { Badge, Card, Direction, KV, PageTitle, Table } from "@/components/ui";
import { getDb } from "@/lib/data";
import { calibrationLabel, inr, pct, prob, when, ORDER_LABEL, SOURCE_LABEL } from "@/lib/format";
import { asOf, evidenceFor, evidenceSource, kgPathsFor } from "@/lib/queries";
import type { Signal, Trade } from "@/lib/types";

export const dynamic = "force-dynamic";

interface Analysis {
  id: number; thesis: string; risks: string[] | null; counter_args: string[] | null; window_days: number | null;
  prediction_source: string | null; analogues: { level: string; n: number; mean: number; hit_rate: number; weight?: number; window: number }[] | null;
  scores_json: { cited_news_ids?: number[]; news_ids?: number[]; panel?: { bull_case?: string; bear_case?: string; judge?: string }; components?: Record<string, number>; options?: { spot?: number; pre_news_spot?: number } } | null;
}

export default async function SignalPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const db = await getDb();
  const [sig] = await db.rows<Signal>("signals", { eq: { id: Number(id) } });
  if (!sig) notFound();
  const [freshness, [an], scen, cands, versions, runs, [trade], paths] = await Promise.all([
    asOf(db, "signals"),
    db.rows<Analysis>("analyses", { eq: { id: sig.analysis_id } }),
    db.rows<{ name: string; probability: number; stock_move_pct: number; timing_days: number }>("scenarios", { eq: { analysis_id: sig.analysis_id } }),
    db.rows<{ strike: number; option_type: string; ask: number | null; ev_pct: number | null; ev_inr: number | null; chosen: boolean; rejected_reason: string | null; breakeven_move_pct: number | null; details_json: { p_t1_before_sl?: number } | null }>(
      "signal_candidates", { eq: { signal_id: sig.id }, order: ["strike", "asc"] }),
    db.rows<{ version: number; score: number; status: string; reason: string | null; ts: string }>("signal_versions", { eq: { signal_id: sig.id }, order: ["version", "asc"] }),
    db.rows<{ agent: string; output_json: { summary?: string } | null; cost_inr: number }>("agent_runs", { eq: { analysis_id: sig.analysis_id } }),
    db.rows<Trade>("paper_trades", { eq: { signal_id: sig.id } }),
    kgPathsFor(db, sig.symbol),
  ]);
  const ids = an?.scores_json?.cited_news_ids ?? an?.scores_json?.news_ids ?? [];
  const evidence = await evidenceFor(db, ids, sig.symbol);
  const panel = an?.scores_json?.panel;
  const calib = calibrationLabel(sig.calibrator_id, sig.calibration_method);
  const chosen = cands.find((c) => c.chosen);
  const opt = an?.scores_json?.options;
  const spot = opt?.spot ?? opt?.pre_news_spot;
  // payoff at expiry as % of premium for stock moves of -6%..+6%
  const payoff =
    chosen?.ask && spot
      ? [-6, -4, -2, 0, 2, 4, 6].map((m) => {
          const s = spot * (1 + m / 100);
          const intrinsic = chosen.option_type === "CE" ? Math.max(0, s - chosen.strike) : Math.max(0, chosen.strike - s);
          return { x: `${m > 0 ? "+" : ""}${m}%`, y: +(((intrinsic - (chosen.ask as number)) / (chosen.ask as number)) * 100).toFixed(1) };
        })
      : [];
  return (
    <>
      <PageTitle title={`${sig.symbol} · S-${sig.id}`}>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <Direction value={sig.direction} />
          <Badge>{sig.status.replaceAll("_", " ").toLowerCase()}</Badge>
          <Badge tone="info">Score {Math.round(sig.score)}</Badge>
          <Badge>{ORDER_LABEL[sig.impact_order ?? 1]}</Badge>
          <Badge>{sig.prediction_source === "panel" ? "Panel" : "Single analyst"}</Badge>
          {sig.enter_after && <Badge tone="warn">Enters after the open ({when(sig.enter_after)})</Badge>}
          {trade && <Link href={`/trades/${trade.id}`}><Badge tone="good">Trade #{trade.id}</Badge></Link>}
        </div>
      </PageTitle>
      <AsOfBar asOf={freshness} now={db.now()} />

      <Card title="Thesis">
        <p className="text-sm leading-relaxed">{an?.thesis ?? "n/a"}</p>
        {(an?.risks?.length ?? 0) > 0 && <p className="mt-2 text-xs text-ink-2">Risks: {an?.risks?.join("; ")}</p>}
      </Card>

      <Card title="Prediction" className="mt-3" action={<Badge tone={calib === "not yet calibrated" ? "warn" : "good"}>{calib}</Badge>}>
        <KV items={[
          ["Predicted move (used)", pct(sig.predicted_move_pct)],
          ["Source", SOURCE_LABEL[sig.predicted_move_source ?? ""] ?? sig.predicted_move_source ?? "n/a"],
          ["Raw AI move", pct(sig.predicted_move_raw_pct)],
          ["Implied move", pct(sig.implied_move_pct, false)],
          ["Edge ratio (raw)", `${sig.edge_ratio?.toFixed(2) ?? "n/a"} (${sig.edge_ratio_raw?.toFixed(2) ?? "n/a"})`],
          ["P(T1 before SL) (raw)", `${prob(sig.p_t1_before_sl)} (${prob(sig.p_t1_before_sl_raw)})`],
          ["EV per position", `${pct(sig.expected_value, true, 0)} · ${inr(sig.expected_value_inr)}`],
          ["Contract", sig.strike ? `${sig.strike} ${sig.option_type} · ${sig.expiry}` : "pending"],
        ]} />
        {calib === "not yet calibrated" && (
          <p className="mt-3 text-xs text-ink-2">Not yet calibrated: gates and EV use the prediction shrunk {Math.round((1 - (sig.shrink_weight ?? 0.5)) * 100)}% toward the option-implied market distribution.</p>
        )}
      </Card>

      {scen.length > 0 && (
        <Card title="Scenario tree" className="mt-3">
          <Table head={["Scenario", "Prob.", "Stock move", "Days"]} rows={scen.map((s) => [s.name, prob(s.probability), pct(s.stock_move_pct), s.timing_days])} />
        </Card>
      )}

      {(an?.analogues?.length ?? 0) > 0 && (
        <Card title="Historical analogues" className="mt-3">
          {an?.analogues?.map((a) => (
            <p key={a.level} className="text-sm">n={a.n} ({a.level}, {a.window}d): avg {pct(a.mean)}, hit {prob(a.hit_rate)}{a.weight != null && `, weight ${prob(a.weight)}`}</p>
          ))}
        </Card>
      )}

      {(panel || runs.length > 0) && (
        <Card title="Panel debate" className="mt-3">
          {panel?.bull_case && <p className="text-sm"><span className="font-medium text-good-ink">Bull: </span>{panel.bull_case}</p>}
          {panel?.bear_case && <p className="mt-2 text-sm"><span className="font-medium text-bad">Bear: </span>{panel.bear_case}</p>}
          {panel?.judge && <p className="mt-2 text-sm"><span className="font-medium">Judge: </span>{panel.judge}</p>}
          {runs.length > 0 && <p className="mt-3 text-xs text-ink-2">Agents: {runs.map((r) => `${r.agent} (${inr(r.cost_inr)})`).join(", ")}</p>}
        </Card>
      )}

      <Card title="Evidence" className="mt-3">
        {evidence.map(({ news, tag }) => {
          const src = evidenceSource(news.source);
          return (
            <div key={news.id} className="border-t border-line py-2.5 first:border-t-0">
              <div className="flex flex-wrap items-center gap-1.5">
                <Badge tone={src.tone}>{src.label}</Badge>
                <span className="text-xs text-muted">{when(news.published_at)}</span>
                {tag?.sentiment != null && <span className="tnum text-xs text-ink-2">sentiment {tag.sentiment > 0 ? "+" : ""}{tag.sentiment.toFixed(2)}</span>}
              </div>
              <a href={news.url} className="mt-1 block text-sm" target="_blank" rel="noreferrer">{news.title}</a>
              {news.body && (news.source.startsWith("kg:") || news.source.startsWith("deep:")) && (
                <p className="mt-1 whitespace-pre-line text-xs text-ink-2">{news.body}</p>
              )}
            </div>
          );
        })}
      </Card>

      {paths.length > 0 && (
        <Card title="Knowledge-graph paths" className="mt-3" action={<Link href={`/graph?symbol=${sig.symbol}`} className="text-xs text-s1">Graph</Link>}>
          {paths.map((p) => (
            <div key={p.id} className="flex items-center justify-between gap-2 border-t border-line py-2 text-sm first:border-t-0">
              <span className="min-w-0 truncate">{p.path_key.replaceAll(">", " › ")}</span>
              <Badge tone={p.role === "propagated" ? "info" : p.role === "support" ? "good" : "neutral"}>
                {p.role === "unknown" ? "sign unknown · support only" : `${p.role} · ${p.sign_basis === "base_rate" ? "base rate" : "default sign"}`}
              </Badge>
            </div>
          ))}
        </Card>
      )}

      {cands.length > 0 && (
        <Card title="Candidate strikes" className="mt-3">
          <Table head={["Strike", "Ask", "EV", "EV ₹", "P(T1)", "Status"]} rows={cands.map((c) => [
            `${c.strike}${c.option_type}`, c.ask?.toFixed(2) ?? "n/a", pct(c.ev_pct, true, 0), inr(c.ev_inr), prob(c.details_json?.p_t1_before_sl),
            c.chosen ? <Badge key="c" tone="good">chosen</Badge> : <span key="r" className="text-xs text-ink-2">{c.rejected_reason}</span>,
          ])} />
          {payoff.length > 0 && (
            <div className="mt-3">
              <p className="mb-1 text-xs text-muted">Payoff at expiry (% of premium) by stock move, chosen strike</p>
              <Bars data={payoff} unit="%" label="Payoff" signed />
            </div>
          )}
        </Card>
      )}

      <Card title="Timeline" className="mt-3">
        {versions.map((v) => (
          <div key={v.version} className="flex items-start justify-between gap-3 border-t border-line py-2 text-sm first:border-t-0">
            <span>v{v.version} · {v.status.toLowerCase()} · score {Math.round(v.score)}<span className="block text-xs text-ink-2">{v.reason}</span></span>
            <span className="shrink-0 text-xs text-muted">{when(v.ts)}</span>
          </div>
        ))}
      </Card>
    </>
  );
}
