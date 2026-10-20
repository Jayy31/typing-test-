import { AsOfBar } from "@/components/asof";
import { LineSeries } from "@/components/charts";
import { Badge, Card, Empty, KV, PageTitle, Stat, Table } from "@/components/ui";
import { getDb } from "@/lib/data";
import { day, inr } from "@/lib/format";
import { asOf } from "@/lib/queries";
import { toggleLabAlerts } from "./actions";

export const dynamic = "force-dynamic";

interface Hyp {
  id: number; name: string; source: string; status: string; ai_memory_risk: boolean; registered_at: string;
  rationale: string; expected_effect: string;
}
interface Spec { id: number; hypothesis_id: number; version: number; name: string; stage: string; spec_yaml: string; change_note?: string | null }
interface Trial {
  id: number; spec_id: number; kind: string; label: string | null; n_trades: number; n_events: number;
  expectancy_pct: number | null; ci_lo?: number | null; ci_hi?: number | null; win_rate?: number | null; created_at: string;
  metrics_json?: { mode?: string; trades?: { exit_day: string | null; pnl_inr: number }[] } | null;
}
interface Check { passed: boolean; [k: string]: unknown }
interface Verdict { id: number; spec_id: number; stage: string; passed: boolean; summary: string; created_at: string; checks_json: { checks?: Record<string, Check>; writeup?: string } }
interface ModeStats { n: number; events?: number; expectancy_pct: number | null; win_rate?: number | null }
interface Portfolio {
  mode: string; trades: number; pnl_inr: number; blocked: { book: string; symbol: string; day: string; reason: string }[];
  equity: [string, number][]; attribution: Record<string, { trades: number; pnl_inr: number }>;
  correlation: Record<string, Record<string, number>>;
}
interface Book {
  id: number; spec_id: number; book: string; status: string; alerts_enabled: boolean; started_at: string;
  stats_json: { eod_incubation?: ModeStats; real_fills?: ModeStats; portfolio?: Portfolio } | null;
}

const TONE: Record<string, "good" | "warn" | "bad" | "info" | "neutral"> = {
  validated: "good", incubating: "info", holdout_pending: "warn", testing: "neutral", stage0: "neutral",
  rejected: "bad", retired: "bad", promoted: "good",
};
const pct = (v: number | null | undefined) => (v == null ? "–" : `${v > 0 ? "+" : ""}${v.toFixed(1)}%`);
const fmt = (v: unknown) => (typeof v === "number" ? String(+v.toFixed(3)) : String(v));

function modeLine(label: string, m?: ModeStats) {
  if (!m || !m.n) return `${label}: no trades yet`;
  return `${label}: ${m.n} trades, expectancy ${pct(m.expectancy_pct)} of premium${m.win_rate != null ? `, win ${Math.round(m.win_rate * 100)}%` : ""}`;
}

export default async function Lab() {
  const db = await getDb();
  const now = db.now();
  const [freshness, hyps, specs, trials, verdicts, books] = await Promise.all([
    asOf(db, "lab"),
    db.rows<Hyp>("lab_hypotheses", { order: ["registered_at", "desc"], limit: 200 }),
    db.rows<Spec>("lab_specs", { order: ["id", "desc"], limit: 500 }),
    db.rows<Trial>("lab_trials", { order: ["created_at", "desc"], limit: 1000 }),
    db.rows<Verdict>("lab_verdicts", { order: ["created_at", "desc"], limit: 500 }),
    db.rows<Book>("lab_books", { order: ["id", "asc"], limit: 50 }),
  ]);
  const specName = new Map(specs.map((s) => [s.id, `${s.name} v${s.version}`]));
  const port = books.find((b) => b.stats_json?.portfolio)?.stats_json?.portfolio;
  const failed = verdicts.filter((v) => !v.passed).length;
  return (
    <>
      <PageTitle title="Strategy Lab" sub="Offline research on pre-cutoff history. Strategies run in their own paper books and never change live rules; promotion goes through a suggestion and a GitHub PR." />
      <AsOfBar asOf={freshness} now={now} />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Hypotheses" value={hyps.length} sub={`${hyps.filter((h) => h.status === "rejected").length} rejected`} />
        <Stat label="Trials run" value={trials.length} sub="every trial counts in the luck tests" />
        <Stat label="Verdicts" value={verdicts.length} sub={`${failed} failed`} />
        <Stat label="Strategy books" value={books.length} sub={`${books.filter((b) => b.status === "validated").length} validated`} />
      </div>

      <Card title="Strategy books" className="mt-3">
        {books.length === 0 ? <Empty>No strategy books yet.</Empty> : books.map((b) => {
          const spec = specs.find((s) => s.id === b.spec_id);
          const eod = trials.find((t) => t.spec_id === b.spec_id && t.kind === "eod");
          let run = 0;
          const curve = (eod?.metrics_json?.trades ?? []).filter((t) => t.exit_day)
            .sort((a, z) => (a.exit_day! < z.exit_day! ? -1 : 1))
            .map((t) => ({ x: t.exit_day!, y: (run += t.pnl_inr) }));
          const vs = verdicts.filter((v) => v.spec_id === b.spec_id);
          return (
            <div key={b.id} className="border-b border-grid py-3 last:border-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{b.book}</span>
                <Badge tone={TONE[b.status] ?? "neutral"}>{b.status}</Badge>
                <span className="text-xs text-ink-2">{spec ? `${spec.name} v${spec.version}` : ""} · since {day(b.started_at)}</span>
              </div>
              <ul className="mt-1 text-sm">
                <li>{modeLine("EOD incubation (bhavcopy prices, modelled spread)", b.stats_json?.eod_incubation)}</li>
                <li>{modeLine("Real fills (live quotes)", b.stats_json?.real_fills)}</li>
              </ul>
              {curve.length > 1 && <LineSeries data={curve} label={`${b.book} cumulative P&L, EOD incubation`} rupees height={140} />}
              {vs.length > 0 && <ul className="mt-1 text-xs text-ink-2">{vs.map((v) => <li key={v.id}>{v.passed ? "✓" : "✗"} {v.summary}</li>)}</ul>}
              {b.status === "validated" ? (
                <form action={toggleLabAlerts} className="mt-2">
                  <input type="hidden" name="id" value={b.id} />
                  <input type="hidden" name="value" value={String(!b.alerts_enabled)} />
                  <button className="rounded border border-grid px-2 py-1 text-xs">Telegram alerts: {b.alerts_enabled ? "on (turn off)" : "off (turn on)"}</button>
                </form>
              ) : <p className="mt-1 text-xs text-ink-2">Alerts can be switched on once the book is validated.</p>}
              {spec && <details className="mt-1"><summary className="cursor-pointer text-xs text-s1">Spec</summary><pre className="overflow-x-auto text-xs">{spec.spec_yaml}</pre></details>}
            </div>
          );
        })}
      </Card>

      <Card title="Portfolio of strategy books" className="mt-3">
        {!port ? <Empty>No portfolio yet.</Empty> : (
          <>
            <KV items={[
              ["Mode", port.mode === "eod_incubation" ? "EOD incubation (not real fills)" : port.mode],
              ["Trades (same-direction overlaps counted once)", port.trades],
              ["P&L", inr(port.pnl_inr)],
              ["Blocked (opposite direction)", port.blocked.length],
            ]} />
            {port.equity.length > 1 && <LineSeries data={port.equity.map(([x, y]) => ({ x, y }))} label="Lab portfolio equity" rupees height={160} />}
            <Table head={["Book", "Trades", "P&L"]} rows={Object.entries(port.attribution).map(([k, v]) => [k, v.trades, inr(v.pnl_inr)])} />
            {Object.keys(port.correlation).length > 1 && (
              <Table className="mt-2" head={["Correlation", ...Object.keys(port.correlation)]} rows={Object.entries(port.correlation).map(([k, row]) => [k, ...Object.keys(port.correlation).map((c) => (row[c] ?? 0).toFixed(2))])} />
            )}
            {port.blocked.length > 0 && <ul className="mt-2 text-xs text-ink-2">{port.blocked.map((x, i) => <li key={i}>Blocked {x.book} {x.symbol} {x.day}: {x.reason}</li>)}</ul>}
          </>
        )}
      </Card>

      <Card title="Hypotheses (pre-registered)" className="mt-3">
        {hyps.length === 0 ? <Empty>No hypotheses yet.</Empty> : (
          <Table head={["Name", "Source", "Stage", "Rationale (frozen)", "Expected"]} rows={hyps.map((h) => [
            <span key="n">{h.name}{h.ai_memory_risk && <Badge tone="warn" className="ml-1">AI memory risk</Badge>}</span>,
            h.source.replace("_", " "), <Badge key="s" tone={TONE[h.status] ?? "neutral"}>{h.status.replace("_", " ")}</Badge>,
            <span key="r" className="text-xs">{h.rationale}</span>, <span key="e" className="text-xs">{h.expected_effect}</span>,
          ])} />
        )}
      </Card>

      <Card title="Luck tests (Stage 0)" className="mt-3">
        {verdicts.filter((v) => v.stage === "stage0").length === 0 ? <Empty>No Stage 0 verdicts yet.</Empty> :
          verdicts.filter((v) => v.stage === "stage0").map((v) => (
            <div key={v.id} className="border-b border-grid py-2 last:border-0">
              <div className="text-sm font-medium">{specName.get(v.spec_id)} <Badge tone={v.passed ? "good" : "bad"}>{v.passed ? "passed" : "failed"}</Badge></div>
              <Table head={["Check", "Result", "Detail"]} rows={Object.entries(v.checks_json.checks ?? {}).map(([k, c]) => [
                k.replaceAll("_", " "), c.passed ? "pass" : "FAIL",
                <span key="d" className="text-xs">{Object.entries(c).filter(([x]) => x !== "passed").map(([x, y]) => `${x} ${fmt(y)}`).join(", ")}</span>,
              ])} />
              {v.checks_json.writeup && <p className="mt-1 text-xs">{v.checks_json.writeup}</p>}
            </div>
          ))}
      </Card>

      <Card title="All trials (including failures)" className="mt-3">
        {trials.length === 0 ? <Empty>No trials yet.</Empty> : (
          <Table head={["Spec", "Kind", "Trades", "Events", "Expectancy", "90% CI"]} rows={trials.map((t) => [
            specName.get(t.spec_id) ?? t.spec_id, `${t.kind}${t.label ? ` (${t.label})` : ""}`, t.n_trades, t.n_events,
            pct(t.expectancy_pct), t.ci_lo == null ? "–" : `${pct(t.ci_lo)} to ${pct(t.ci_hi)}`,
          ])} />
        )}
      </Card>
    </>
  );
}
