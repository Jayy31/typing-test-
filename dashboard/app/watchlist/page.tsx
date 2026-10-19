import { AsOfBar } from "@/components/asof";
import {
  Badge,
  Card,
  Empty,
  PageTitle,
  Row,
  Stat,
  Table,
} from "@/components/ui";
import { getDb } from "@/lib/data";
import { inr, pct } from "@/lib/format";
import { asOf, trades, unrealised } from "@/lib/queries";
import type { Signal, Trade } from "@/lib/types";

export const dynamic = "force-dynamic";

const GATE: Record<string, string> = {
  score: "Score",
  edge_ratio: "Edge ratio",
  p_t1_before_sl: "P(T1 before SL)",
  ev_pct: "Min EV %",
};

function stats(ts: Trade[]) {
  const closed = ts.filter((t) => t.status === "CLOSED");
  const n = closed.length;
  const wins = closed.filter((t) => (t.pnl ?? 0) > 0).length;
  return {
    n,
    open: ts.length - n,
    win: n ? wins / n : null,
    exp: n ? closed.reduce((a, t) => a + (t.pnl_pct ?? 0), 0) / n : null,
    pnl: closed.reduce((a, t) => a + (t.pnl ?? 0), 0),
  };
}

export default async function Watchlist() {
  const db = await getDb();
  const [freshness, main, watch, sigs] = await Promise.all([
    asOf(db, "watchlist"),
    trades(db, undefined, "main"),
    trades(db, undefined, "watchlist"),
    db.rows<
      Signal & {
        flags_json?: {
          watchlist?: { gate: string; value: number; required: number };
        };
      }
    >("signals"),
  ]);
  const sig = new Map(sigs.map((s) => [s.id, s]));
  const gateOf = (t: Trade) =>
    sig.get(t.signal_id)?.flags_json?.watchlist?.gate ??
    (t as Trade & { watch_gate?: string }).watch_gate ??
    "?";
  const m = stats(main),
    w = stats(watch);
  const byGate = new Map<string, Trade[]>();
  watch.forEach((t) =>
    byGate.set(gateOf(t), [...(byGate.get(gateOf(t)) ?? []), t]),
  );
  return (
    <>
      <PageTitle
        title="Watchlist book"
        sub="Near-misses: passed every hard check, missed exactly one soft gate by a small margin. Paper-traded with the same rules, kept apart from the main book."
      />
      <AsOfBar asOf={freshness} now={db.now()} />
      <Card title="Main book vs watchlist book (closed trades)">
        <Table
          head={["Book", "Closed", "Win rate", "Expectancy", "P&L"]}
          rows={[
            [
              "Main",
              m.n,
              m.win == null ? "n/a" : `${Math.round(m.win * 100)}%`,
              pct(m.exp),
              inr(m.pnl, true),
            ],
            [
              "Watchlist",
              w.n,
              w.win == null ? "n/a" : `${Math.round(w.win * 100)}%`,
              pct(w.exp),
              inr(w.pnl, true),
            ],
          ]}
        />
        <p className="mt-2 text-xs text-muted">
          If the watchlist keeps doing as well as the main book, a gate may be
          too strict; the learning loop checks this before suggesting a change.
        </p>
      </Card>
      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat
          label="Watchlist trades"
          value={w.n + w.open}
          sub={`${w.open} open`}
        />
        <Stat
          label="Watchlist expectancy"
          value={pct(w.exp)}
          tone={(w.exp ?? 0) >= 0 ? "good" : "bad"}
        />
        <Stat
          label="Main expectancy"
          value={pct(m.exp)}
          tone={(m.exp ?? 0) >= 0 ? "good" : "bad"}
        />
        <Stat
          label="Not in P&L"
          value="Watchlist"
          sub="separate from main stats"
        />
      </div>
      <Card title="By the gate that was missed" className="mt-3">
        {byGate.size === 0 && <Empty>No near-misses yet.</Empty>}
        {byGate.size > 0 && (
          <Table
            head={["Gate missed", "Trades", "Win rate", "Expectancy"]}
            rows={[...byGate].map(([g, ts]) => {
              const s = stats(ts);
              return [
                GATE[g] ?? g,
                s.n + s.open,
                s.win == null ? "n/a" : `${Math.round(s.win * 100)}%`,
                pct(s.exp),
              ];
            })}
          />
        )}
      </Card>
      <Card title="Watchlist trades" className="mt-3">
        {watch.length === 0 && <Empty>No watchlist trades.</Empty>}
        {watch.map((t) => (
          <Row key={t.id} href={`/trades/${t.id}`}>
            <div className="min-w-0">
              <div className="font-medium">
                {t.symbol} {t.strike}
                {t.option_type}
              </div>
              <div className="text-xs text-ink-2">
                missed: {GATE[gateOf(t)] ?? gateOf(t)}
              </div>
            </div>
            <div className="text-right">
              {t.status === "OPEN" ? (
                <Badge tone="info">open {inr(unrealised(t), true)}</Badge>
              ) : (
                <span
                  className={
                    (t.pnl ?? 0) >= 0 ? "tnum text-good-ink" : "tnum text-bad"
                  }
                >
                  {pct(t.pnl_pct)}
                </span>
              )}
            </div>
          </Row>
        ))}
      </Card>
    </>
  );
}
