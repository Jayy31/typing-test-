import Link from "next/link";
import { AsOfBar } from "@/components/asof";
import { Badge, Card, Empty, PageTitle } from "@/components/ui";
import { getDb } from "@/lib/data";
import { pct } from "@/lib/format";
import { asOf } from "@/lib/queries";

export const dynamic = "force-dynamic";

interface Report { id: number; month: string; ai_summary_status: string | null; data_json: { trades?: { all?: { n?: number; expectancy_pct?: number } } } }

export default async function Reports() {
  const db = await getDb();
  const [freshness, rows] = await Promise.all([asOf(db, "reports"), db.rows<Report>("monthly_reports", { order: ["month", "desc"], limit: 24 })]);
  return (
    <>
      <PageTitle title="Monthly reports" sub="1st of each month: what worked, what didn't, scorecard, AI vs rules, suggestions. Also sent as a PDF on Telegram." />
      <AsOfBar asOf={freshness} now={db.now()} />
      <Card>
        {rows.length === 0 && <Empty>No report yet: the first one arrives on the 1st of next month.</Empty>}
        {rows.map((r) => {
          const all = r.data_json.trades?.all ?? {};
          return (
            <Link key={r.id} href={`/reports/${r.month.slice(0, 7)}`} className="flex items-center justify-between border-t border-line py-3 first:border-t-0">
              <span><span className="block font-medium">{new Date(r.month).toLocaleDateString("en-IN", { month: "long", year: "numeric" })}</span>
                <span className="tnum text-xs text-ink-2">{all.n ?? 0} trades · expectancy {pct(all.expectancy_pct)}</span></span>
              <Badge>{r.ai_summary_status === "ok" ? "with summary" : "figures only"}</Badge>
            </Link>
          );
        })}
      </Card>
    </>
  );
}
