import Link from "next/link";
import { AsOfBar } from "@/components/asof";
import { Badge, Card, Direction, Empty, PageTitle } from "@/components/ui";
import { getDb } from "@/lib/data";
import { calibrationLabel, pct, predictor, prob, when, ORDER_LABEL } from "@/lib/format";
import { asOf } from "@/lib/queries";
import type { Signal } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function Signals() {
  const db = await getDb();
  const [freshness, all] = await Promise.all([
    asOf(db, "signals"),
    db.rows<Signal>("signals", { order: ["updated_at", "desc"], limit: 60 }),
  ]).then(
    ([f, rows]) =>
      [f, rows.filter((s) => (s.book ?? "main") === "main")] as const,
  );
  const live = all
    .filter((s) =>
      ["NEW", "ACTIVE", "UPGRADED", "DOWNGRADED"].includes(s.status),
    )
    .sort((a, b) => b.score - a.score);
  const past = all.filter((s) => !live.includes(s));
  return (
    <>
      <PageTitle
        title="Signals"
        sub="Ranked by score. Probabilities and EV use the prediction shrunk toward the market until a calibrator exists."
      />
      <AsOfBar asOf={freshness} now={db.now()} />
      <div className="space-y-3">
        {live.length === 0 && <Empty>No live signals.</Empty>}
        {live.map((s) => (
          <Link key={s.id} href={`/signals/${s.id}`} className="block">
            <Card>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-base font-semibold">{s.symbol}</span>
                    <Direction value={s.direction} />
                    <Badge>S-{s.id}</Badge>
                    {s.enter_after && (
                      <Badge tone="warn">Enters after the open</Badge>
                    )}
                  </div>
                  <div className="mt-1 text-sm text-ink-2">
                    {s.strike
                      ? `${s.strike} ${s.option_type} · ${s.expiry}`
                      : `Pricing ${s.pricing_status ?? "pending"}`}
                  </div>
                </div>
                <div className="text-right">
                  <div className="tnum text-2xl font-semibold">
                    {Math.round(s.score)}
                  </div>
                  <div className="text-xs text-muted">score</div>
                </div>
              </div>
              <div className="tnum mt-3 grid grid-cols-3 gap-2 text-sm">
                <div>
                  <div className="text-xs text-muted">EV</div>
                  {pct(s.expected_value, true, 0)}
                </div>
                <div>
                  <div className="text-xs text-muted">P(T1 before SL)</div>
                  {prob(s.p_t1_before_sl)}
                </div>
                <div>
                  <div className="text-xs text-muted">Edge ratio</div>
                  {s.edge_ratio?.toFixed(2) ?? "n/a"}
                </div>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                <Badge>
                  {calibrationLabel(s.calibrator_id, s.calibration_method)}
                </Badge>
                <Badge>{ORDER_LABEL[s.impact_order ?? 1]}</Badge>
                <Badge>
                  {predictor(s.prediction_source)}
                </Badge>
              </div>
            </Card>
          </Link>
        ))}
      </div>
      <Card title="Recent (not live)" className="mt-4">
        {past.map((s) => (
          <Link
            key={s.id}
            href={`/signals/${s.id}`}
            className="flex items-center justify-between border-t border-line py-2 text-sm first:border-t-0"
          >
            <span>
              S-{s.id} {s.symbol}{" "}
              <span className="text-ink-2">{s.direction}</span>
            </span>
            <span className="text-xs text-ink-2">
              {s.status.replaceAll("_", " ").toLowerCase()} ·{" "}
              {when(s.updated_at)}
            </span>
          </Link>
        ))}
      </Card>
    </>
  );
}
