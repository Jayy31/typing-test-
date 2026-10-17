import Link from "next/link";
import { AsOfBar } from "@/components/asof";
import { Card, PageTitle, Table } from "@/components/ui";
import { getDb } from "@/lib/data";
import { pct, prob } from "@/lib/format";
import { asOf } from "@/lib/queries";

export const dynamic = "force-dynamic";

interface Lib { id: number; event_type: string; level_key: string; regime: string; window: number; n: number; mean: number; hit_rate: number; p25: number; p75: number;
  examples_json: { date: string; symbol: string; direction: string; abnormal_pct: number; title: string }[] | null }

export default async function Events({ searchParams }: { searchParams: Promise<{ q?: string; w?: string }> }) {
  const { q = "", w = "3" } = await searchParams;
  const db = await getDb();
  const [freshness, rows] = await Promise.all([asOf(db, "events"), db.rows<Lib>("event_library", { order: ["n", "desc"], limit: 2000 })]);
  const shown = rows.filter((r) => r.window === Number(w) && r.regime === "all" && (!q || r.event_type.includes(q) || r.level_key.includes(q)));
  const direct = shown.filter((r) => !r.event_type.startsWith("kg:"));
  const graph = shown.filter((r) => r.event_type.startsWith("kg:"));
  const examples = shown.flatMap((r) => r.examples_json ?? []).slice(0, 10);
  const table = (list: Lib[]) => (
    <Table head={["Event type", "Level", "n", "Avg move", "Hit", "p25…p75"]} rows={list.map((r) => [
      r.event_type, r.level_key, r.n, pct(r.mean), prob(r.hit_rate), `${pct(r.p25)} … ${pct(r.p75)}`,
    ])} />
  );
  return (
    <>
      <PageTitle title="Event library" sub="How stocks actually moved after each event type (abnormal returns, in the event's direction). Below ANALOGUE_MIN_N events a base rate is not used." />
      <form className="mb-3 flex gap-2" action="/events">
        <input name="q" defaultValue={q} placeholder="Search event type or sector" className="min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 py-2 text-sm" />
        <select name="w" defaultValue={w} className="rounded-lg border border-line bg-surface px-2 text-sm" aria-label="Window">
          {[0, 1, 3, 5, 10].map((x) => <option key={x} value={x}>{x}d</option>)}
        </select>
        <button className="rounded-lg bg-s1 px-3 text-sm text-white">Go</button>
      </form>
      <AsOfBar asOf={freshness} now={db.now()} />
      <Card title="Direct events">{table(direct)}</Card>
      <Card title="Linked stocks (knowledge graph)" className="mt-3">{table(graph)}</Card>
      <Card title="Recent examples" className="mt-3">
        {examples.map((e, i) => (
          <p key={i} className="border-t border-line py-2 text-sm first:border-t-0">
            {e.date} <Link href={`/news?symbol=${e.symbol}`} className="text-s1">{e.symbol}</Link> ({e.direction}): {pct(e.abnormal_pct)} · <span className="text-ink-2">{e.title}</span>
          </p>
        ))}
      </Card>
    </>
  );
}
