import Link from "next/link";
import { AsOfBar } from "@/components/asof";
import { Badge, Card, Direction, Empty, Row, Stat } from "@/components/ui";
import { getDb } from "@/lib/data";
import { inr, pct, ORDER_LABEL } from "@/lib/format";
import { asOf, istDayStart, liveSignals, reviewItems, systemHealth, trades, unrealised } from "@/lib/queries";
import type { Trade } from "@/lib/types";

export const dynamic = "force-dynamic";

const HEALTH = { green: ["good", "All systems normal"], amber: ["warn", "Needs attention"], red: ["bad", "Problem"] } as const;

export default async function Today() {
  const db = await getDb();
  const now = db.now();
  const [freshness, sigs, open, closed, health, review] = await Promise.all([
    asOf(db, "today"), liveSignals(db), trades(db, "OPEN"), trades(db, "CLOSED"), systemHealth(db), reviewItems(db),
  ]);
  const dayStart = istDayStart(now);
  const today = new Date(now.getTime() + 330 * 60_000).toISOString().slice(0, 10);
  const events = await db.rows<{ id: number; symbol: string | null; event_type: string; details: Record<string, string> | null }>(
    "events_calendar", { eq: { event_date: today } },
  );
  const closedToday = closed.filter((t: Trade) => (t.exit_ts ?? "") >= dayStart);
  const unreal = open.reduce((a, t) => a + unrealised(t), 0);
  const realised = closedToday.reduce((a, t) => a + (t.pnl ?? 0), 0);
  const [tone, label] = HEALTH[health.level];
  return (
    <>
      <AsOfBar asOf={freshness} now={now} />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Open P&L" value={inr(unreal, true)} tone={unreal >= 0 ? "good" : "bad"} sub={`${open.length} open trade(s)`} />
        <Stat label="Realised today" value={inr(realised, true)} sub={`${closedToday.length} closed`} />
        <Stat label="Live signals" value={sigs.length} sub={`${sigs.filter((s) => s.enter_after).length} entering at open`} />
        <Stat label="AI spend today" value={inr(health.aiToday)} sub="of the daily budget" />
      </div>

      <Link href="/health" className="mt-3 block" data-testid="health">
        <Card>
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm font-semibold">System health</span>
            <Badge tone={tone}>{health.level === "green" ? "● " : "▲ "}{label}</Badge>
          </div>
          {health.problems.slice(0, 3).map((p) => (
            <p key={p} className="mt-1.5 truncate text-xs text-ink-2">{p}</p>
          ))}
        </Card>
      </Link>

      <Card title="Live signals" className="mt-3" action={<Link href="/signals" className="text-xs text-s1">All</Link>}>
        {sigs.length === 0 && <Empty>No live signals.</Empty>}
        {sigs.map((s) => (
          <Row key={s.id} href={`/signals/${s.id}`}>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-medium">{s.symbol}</span>
                <Direction value={s.direction} />
              </div>
              <div className="mt-0.5 truncate text-xs text-ink-2">
                {s.strike ? `${s.strike}${s.option_type}` : "pricing pending"} · EV {pct(s.expected_value, true, 0)} · {ORDER_LABEL[s.impact_order ?? 1]}
                {s.enter_after && " · enters after the open"}
              </div>
            </div>
            <span className="tnum text-lg font-semibold">{Math.round(s.score)}</span>
          </Row>
        ))}
      </Card>

      <Card title="Open trades" className="mt-3" action={<Link href="/trades" className="text-xs text-s1">All</Link>}>
        {open.length === 0 && <Empty>No open trades.</Empty>}
        {open.map((t) => {
          const u = unrealised(t);
          const p = ((t.last_premium ?? t.entry_premium) / t.entry_premium - 1) * 100;
          return (
            <Row key={t.id} href={`/trades/${t.id}`}>
              <div className="min-w-0">
                <div className="font-medium">{t.symbol} {t.strike}{t.option_type}</div>
                <div className="text-xs text-ink-2">{t.state_json?.targets_hit ?? 0} target(s) hit · {t.lots} lots</div>
              </div>
              <div className="text-right">
                <div className={u >= 0 ? "tnum font-semibold text-good-ink" : "tnum font-semibold text-bad"}>{inr(u, true)}</div>
                <div className="tnum text-xs text-ink-2">{pct(p)}</div>
              </div>
            </Row>
          );
        })}
      </Card>

      <Card title="Today's events and results" className="mt-3" action={<Link href="/calendar" className="text-xs text-s1">Calendar</Link>}>
        {events.length === 0 && <Empty>Nothing scheduled.</Empty>}
        {events.map((e) => (
          <Row key={e.id}>
            <span className="text-sm">{e.symbol ?? e.details?.name ?? "Market"}</span>
            <Badge tone={e.event_type === "results" ? "info" : "neutral"}>{e.event_type}</Badge>
          </Row>
        ))}
      </Card>

      <Card title="Needs review" className="mt-3">
        {review.length === 0 && <Empty>Nothing to review.</Empty>}
        {review.map((r) => (
          <Row key={r.text} href={r.href}>
            <span className="min-w-0 truncate text-sm">{r.text}</span>
            <Badge tone="warn">{r.kind}</Badge>
          </Row>
        ))}
      </Card>
    </>
  );
}
