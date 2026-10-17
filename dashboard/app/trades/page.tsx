import Link from "next/link";
import { AsOfBar } from "@/components/asof";
import { Badge, Card, Empty, PageTitle } from "@/components/ui";
import { getDb } from "@/lib/data";
import { inr, pct, when } from "@/lib/format";
import { asOf, trades, unrealised } from "@/lib/queries";

export const dynamic = "force-dynamic";

const FILL_TONE = { fillable: "good", partial: "warn", unfillable: "bad" } as const;

export default async function Trades({ searchParams }: { searchParams: Promise<{ tab?: string }> }) {
  const { tab = "open" } = await searchParams;
  const db = await getDb();
  const [freshness, list] = await Promise.all([asOf(db, "trades"), trades(db, tab === "closed" ? "CLOSED" : "OPEN")]);
  return (
    <>
      <PageTitle title="Paper trades" />
      <div className="mb-3 flex gap-2" role="tablist">
        {["open", "closed"].map((t) => (
          <Link key={t} href={`/trades?tab=${t}`} role="tab" aria-selected={tab === t}
            className={tab === t ? "rounded-full bg-s1 px-3 py-1 text-sm text-white" : "rounded-full bg-grid px-3 py-1 text-sm text-ink-2"}>
            {t === "open" ? "Open" : "Closed"}
          </Link>
        ))}
      </div>
      <AsOfBar asOf={freshness} now={db.now()} />
      <div className="space-y-3">
        {list.length === 0 && <Empty>No {tab} trades.</Empty>}
        {list.map((t) => {
          const pnl = t.status === "OPEN" ? unrealised(t) : t.pnl ?? 0;
          const p = t.status === "OPEN" ? ((t.last_premium ?? t.entry_premium) / t.entry_premium - 1) * 100 : t.pnl_pct;
          return (
            <Link key={t.id} href={`/trades/${t.id}`} className="block">
              <Card>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="font-semibold">{t.symbol} {t.strike} {t.option_type}</div>
                    <div className="mt-0.5 text-xs text-ink-2">
                      {t.lots} lots × {t.lot_size} · entry {t.entry_premium} · {t.status === "OPEN" ? `now ${t.last_premium ?? "n/a"}` : `${t.exit_reason} at ${t.exit_premium}`}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className={pnl >= 0 ? "tnum font-semibold text-good-ink" : "tnum font-semibold text-bad"}>{inr(pnl, true)}</div>
                    <div className="tnum text-xs text-ink-2">{pct(p)}</div>
                  </div>
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {t.fillability && <Badge tone={FILL_TONE[t.fillability as keyof typeof FILL_TONE] ?? "neutral"}>{t.fillability}</Badge>}
                  <Badge>{t.booking_mode ?? "per_lot"}</Badge>
                  {t.status === "CLOSED" && <Badge>closed {when(t.exit_ts)}</Badge>}
                </div>
              </Card>
            </Link>
          );
        })}
      </div>
    </>
  );
}
