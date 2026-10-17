import Link from "next/link";
import { AsOfBar } from "@/components/asof";
import { Badge, Card, Empty, PageTitle, Table } from "@/components/ui";
import { getDb } from "@/lib/data";
import { when } from "@/lib/format";
import { asOf } from "@/lib/queries";
import type { KgPath } from "@/lib/types";

export const dynamic = "force-dynamic";

interface Node { id: number; symbol: string | null; name: string; kind: string }
interface Edge { id: number; src: number; dst: number; type: string; weight: number; exposure_kind: string | null; exposure_pct: number | null; origin: string | null }

export default async function Graph({ searchParams }: { searchParams: Promise<{ symbol?: string }> }) {
  const { symbol } = await searchParams;
  const db = await getDb();
  const [freshness, nodes, edges, paths] = await Promise.all([
    asOf(db, "graph"), db.rows<Node>("kg_nodes"), db.rows<Edge>("kg_edges"),
    db.rows<KgPath>("kg_paths", { order: ["created_at", "desc"], limit: 30 }),
  ]);
  const name = new Map(nodes.map((n) => [n.id, n.symbol ?? n.name]));
  const focus = symbol ?? nodes[0]?.symbol ?? "";
  const focusId = nodes.find((n) => n.symbol === focus)?.id;
  const out = edges.filter((e) => e.src === focusId);
  const into = edges.filter((e) => e.dst === focusId);
  const companies = nodes.filter((n) => n.kind === "company");
  return (
    <>
      <PageTitle title="Knowledge graph" sub="Who an event on one stock can move, with edge weight (exposure-scaled) and source." />
      <AsOfBar asOf={freshness} now={db.now()} />
      <div className="-mx-4 mb-3 flex gap-1.5 overflow-x-auto px-4 pb-1">
        {companies.map((n) => (
          <Link key={n.id} href={`/graph?symbol=${n.symbol}`}><Badge tone={n.symbol === focus ? "info" : "neutral"}>{n.symbol}</Badge></Link>
        ))}
      </div>
      <Card title={`${focus}: edges out (an event on ${focus} moves…)`}>
        {out.length === 0 ? <Empty>No edges.</Empty> : (
          <Table head={["Target", "Relation", "Weight", "Exposure", "Source"]} rows={out.map((e) => [
            <Link key="t" className="text-s1" href={`/graph?symbol=${name.get(e.dst)}`}>{name.get(e.dst)}</Link>,
            e.type.replaceAll("_", " "), e.weight.toFixed(2),
            e.exposure_pct != null ? `${e.exposure_pct}% ${e.exposure_kind?.replaceAll("_", " ")}` : <span key="m" className="text-ink-2">missing (low weight)</span>, e.origin ?? "",
          ])} />
        )}
      </Card>
      <Card title={`${focus}: edges in`} className="mt-3">
        {into.length === 0 ? <Empty>No edges.</Empty> : (
          <Table head={["From", "Relation", "Weight"]} rows={into.map((e) => [name.get(e.src), e.type.replaceAll("_", " "), e.weight.toFixed(2)])} />
        )}
      </Card>
      <Card title="Recent propagations" className="mt-3">
        {paths.map((p) => (
          <div key={p.id} className="flex items-center justify-between gap-2 border-t border-line py-2 text-sm first:border-t-0">
            <span className="min-w-0"><span className="block truncate">{p.path_key.replaceAll(">", " › ")}</span><span className="text-xs text-muted">{when(p.created_at)}</span></span>
            <Badge tone={p.role === "propagated" ? "info" : p.role === "support" ? "good" : p.role === "capped" ? "warn" : "neutral"}>
              {p.role === "unknown" ? "sign unknown" : `${p.role} · ${p.sign_basis === "base_rate" ? "base rate" : "default"}`}
            </Badge>
          </div>
        ))}
      </Card>
    </>
  );
}
