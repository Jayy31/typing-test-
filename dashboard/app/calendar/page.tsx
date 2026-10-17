import { AsOfBar } from "@/components/asof";
import { Badge, Card, PageTitle } from "@/components/ui";
import { getDb } from "@/lib/data";
import { day } from "@/lib/format";
import { asOf } from "@/lib/queries";

export const dynamic = "force-dynamic";

export default async function Calendar() {
  const db = await getDb();
  const now = db.now();
  const today = new Date(now.getTime() + 330 * 60_000).toISOString().slice(0, 10);
  const [freshness, events, releases] = await Promise.all([
    asOf(db, "calendar"),
    db.rows<{ id: number; symbol: string | null; event_type: string; event_date: string; details: Record<string, string> | null }>(
      "events_calendar", { gte: ["event_date", today], order: ["event_date", "asc"], limit: 100 }),
    db.rows<{ id: number; name: string; period: string; value: number | null; prior: number | null; released_at: string }>(
      "macro_releases", { order: ["released_at", "desc"], limit: 10 }),
  ]);
  const byDay = new Map<string, typeof events>();
  events.forEach((e) => byDay.set(e.event_date, [...(byDay.get(e.event_date) ?? []), e]));
  return (
    <>
      <PageTitle title="Event calendar" sub="Results, holidays, expiries and macro releases." />
      <AsOfBar asOf={freshness} now={now} />
      <div className="space-y-3">
        {[...byDay].map(([d, evs]) => (
          <Card key={d} title={d === today ? `Today · ${day(d)}` : day(d)}>
            {evs.map((e) => (
              <div key={e.id} className="flex items-center justify-between border-t border-line py-2 text-sm first:border-t-0">
                <span>{e.symbol ?? e.details?.name ?? "Market"}</span>
                <Badge tone={e.event_type === "results" ? "info" : e.event_type === "holiday" ? "warn" : "neutral"}>{e.event_type}</Badge>
              </div>
            ))}
          </Card>
        ))}
      </div>
      <Card title="Recent macro releases" className="mt-3">
        {releases.map((r) => (
          <div key={r.id} className="tnum flex justify-between border-t border-line py-2 text-sm first:border-t-0">
            <span>{r.name.toUpperCase()} · {r.period.slice(0, 7)}</span>
            <span>{r.value} <span className="text-xs text-ink-2">(prior {r.prior ?? "n/a"})</span></span>
          </div>
        ))}
      </Card>
    </>
  );
}
