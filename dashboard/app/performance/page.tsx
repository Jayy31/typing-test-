import { AsOfBar } from "@/components/asof";
import { Bars } from "@/components/charts";
import { Card, PageTitle, Stat, Table } from "@/components/ui";
import { getDb } from "@/lib/data";
import { inr, pct, predictor, ORDER_LABEL } from "@/lib/format";
import { asOf, trades } from "@/lib/queries";
import type { Signal, Trade } from "@/lib/types";

export const dynamic = "force-dynamic";

function summary(ts: Trade[]) {
  const n = ts.length;
  const wins = ts.filter((t) => (t.pnl ?? 0) > 0).length;
  const exp = n ? ts.reduce((a, t) => a + (t.pnl_pct ?? 0), 0) / n : null;
  return {
    n,
    win: n ? wins / n : null,
    exp,
    pnl: ts.reduce((a, t) => a + (t.pnl ?? 0), 0),
  };
}

type Positioning = {
  evidence?: {
    feature: string;
    state: string;
    basis: string;
    weight: number;
  }[];
};

function positioningLabel(p: Positioning | undefined): string {
  const ev = p?.evidence ?? [];
  if (!ev.length) return "none";
  const used = ev
    .filter((e) => e.weight > 0)
    .sort((a, b) => b.weight - a.weight)[0];
  if (used) return `${used.feature}=${used.state} (base rate)`;
  const b = ev.find((e) => e.feature === "oi_buildup");
  return b ? `oi_buildup=${b.state} (support)` : "support only";
}

export default async function Performance({
  searchParams,
}: {
  searchParams: Promise<{ fills?: string }>;
}) {
  const { fills = "all" } = await searchParams;
  const db = await getDb();
  const [freshness, all, sigs, analyses] = await Promise.all([
    asOf(db, "performance"),
    trades(db, "CLOSED"),
    db.rows<Signal>("signals"),
    db.rows<{ id: number; scores_json: Record<string, unknown> | null }>(
      "analyses",
    ),
  ]);
  const an = new Map(analyses.map((a) => [a.id, a.scores_json ?? {}]));
  const js = (t: Trade) =>
    an.get(sig.get(t.signal_id)?.analysis_id ?? -1) ?? {};
  const closed =
    fills === "fillable"
      ? all.filter((t) => t.fillability !== "unfillable")
      : all;
  const sig = new Map(sigs.map((s) => [s.id, s]));
  const by = (key: (t: Trade) => string) => {
    const m = new Map<string, Trade[]>();
    closed.forEach((t) => m.set(key(t), [...(m.get(key(t)) ?? []), t]));
    return [...m].map(([k, v]) => [k, summary(v)] as const);
  };
  const s = summary(closed);
  const groups: [string, (t: Trade) => string][] = [
    [
      "Prediction",
      (t) => predictor(sig.get(t.signal_id)?.prediction_source),
    ],
    [
      "AI preset",
      (t) =>
        (sig.get(t.signal_id) as { ai_preset?: string | null } | undefined)
          ?.ai_preset ?? "n/a (panel / single)",
    ],
    [
      "Impact order",
      (t) => ORDER_LABEL[sig.get(t.signal_id)?.impact_order ?? 1],
    ],
    ["Sector", (t) => sig.get(t.signal_id)?.sector ?? "?"],
    [
      "Score band",
      (t) => {
        const sc = sig.get(t.signal_id)?.score ?? 0;
        return sc >= 75 ? "75+" : sc >= 65 ? "65-74" : "<65";
      },
    ],
    ["Exit", (t) => t.exit_reason ?? "?"],
    ["Market regime", (t) => String(js(t).regime ?? "unknown")],
    [
      "Positioning",
      (t) => positioningLabel(js(t).positioning as Positioning | undefined),
    ],
  ];
  return (
    <>
      <PageTitle
        title="Performance"
        sub="Closed paper trades, % of premium after costs."
      />
      <div className="mb-3 flex gap-2">
        {[
          ["all", "All trades"],
          ["fillable", "Excluding unfillable"],
        ].map(([k, l]) => (
          <a
            key={k}
            href={`/performance?fills=${k}`}
            className={
              fills === k
                ? "rounded-full bg-s1 px-3 py-1 text-sm text-white"
                : "rounded-full bg-grid px-3 py-1 text-sm text-ink-2"
            }
          >
            {l}
          </a>
        ))}
      </div>
      <AsOfBar asOf={freshness} now={db.now()} />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Trades" value={s.n} />
        <Stat
          label="Win rate"
          value={s.win == null ? "n/a" : `${Math.round(s.win * 100)}%`}
        />
        <Stat
          label="Expectancy"
          value={pct(s.exp)}
          tone={(s.exp ?? 0) >= 0 ? "good" : "bad"}
        />
        <Stat
          label="P&L"
          value={inr(s.pnl, true)}
          tone={s.pnl >= 0 ? "good" : "bad"}
        />
      </div>
      <Card title="P&L per trade (% of premium)" className="mt-3">
        <Bars
          data={closed
            .slice()
            .reverse()
            .map((t) => ({
              x: `${t.symbol}`,
              y: +(t.pnl_pct ?? 0).toFixed(1),
            }))}
          unit="%"
          label="P&L"
          signed
        />
      </Card>
      {groups.map(([title, key]) => (
        <Card key={title} title={`By ${title.toLowerCase()}`} className="mt-3">
          <Table
            head={[title, "Trades", "Win rate", "Expectancy", "P&L"]}
            rows={by(key).map(([k, v]) => [
              k,
              v.n,
              v.win == null ? "n/a" : `${Math.round(v.win * 100)}%`,
              pct(v.exp),
              inr(v.pnl, true),
            ])}
          />
        </Card>
      ))}
    </>
  );
}
