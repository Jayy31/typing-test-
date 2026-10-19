import Link from "next/link";
import { Badge, Card, KV } from "@/components/ui";
import type { Db } from "@/lib/db";
import { inr, when } from "@/lib/format";

interface Run {
  id: number; role: string; preset: string; model: string; effort: string | null; status: string;
  limited_reason: string | null; cost_inr: number; research_file_id: number | null; bear_case: string | null;
  tool_calls: number; web_searches: number; web_fetches: number; duration_s: number | null; error?: string | null;
  sources_json: { url: string; title?: string; kind?: string }[] | null;
  lessons_json: { id: number; text: string; applied?: boolean }[] | null;
  output_json: { base_rate?: string; adjustments?: string[]; cited_urls?: string[] } | null;
}

/** Phase 13: research file used, tools called, sources, bear case, lessons applied, model, cost. */
export async function ResearchCard({ db, runId, used }: { db: Db; runId: number; used: boolean }) {
  const [run] = await db.rows<Run>("research_runs", { eq: { id: runId } });
  if (!run) return null;
  const [calls, files] = await Promise.all([
    db.rows<{ tool: string; seq: number }>("research_tool_calls", { eq: { run_id: runId }, order: ["seq", "asc"] }),
    run.research_file_id
      ? db.rows<{ id: number; subject: string; version: number; created_at: string; model: string }>("research_files", { eq: { id: run.research_file_id } })
      : Promise.resolve([]),
  ]);
  const counts = new Map<string, number>();
  calls.forEach((c) => counts.set(c.tool, (counts.get(c.tool) ?? 0) + 1));
  const f = files[0];
  const out = run.output_json ?? {};
  const cited = out.cited_urls ?? [];
  const applied = (run.lessons_json ?? []).filter((l) => l.applied);
  return (
    <Card title="Research agent" className="mt-3" action={
      <Badge tone={run.status === "ok" ? "good" : run.status === "limited" ? "warn" : "bad"}>
        {run.status}{run.limited_reason ? ` (${run.limited_reason})` : ""}
      </Badge>
    }>
      {!used && <p className="mb-2 rounded-lg bg-warn-wash px-3 py-2 text-sm">This run failed; the prediction came from the single analyst. {run.error}</p>}
      <KV items={[
        ["Model", `${run.model} · ${run.role} · preset ${run.preset} · effort ${run.effort ?? "default"}`],
        ["Cost", `${inr(run.cost_inr)}${run.duration_s ? ` · ${Math.round(run.duration_s)}s` : ""}`],
        ["Research file", f ? <Link key="f" href={`/research?file=${f.id}`} className="text-s1">{f.subject} v{f.version} ({when(f.created_at)})</Link> : "none yet"],
        ["Tools called", [...counts].map(([k, v]) => `${k} ×${v}`).join(", ") || "none"],
        ["Base rate", out.base_rate ?? "n/a"],
      ]} />
      {(out.adjustments ?? []).length > 0 && (
        <ul className="mt-2 list-disc pl-5 text-sm">{(out.adjustments ?? []).map((a) => <li key={a}>{a}</li>)}</ul>
      )}
      {run.bear_case && (
        <p className="mt-2 text-sm"><span className="font-medium text-bad">Bear case: </span>{run.bear_case}</p>
      )}
      {applied.length > 0 && (
        <p className="mt-2 text-sm"><span className="font-medium">Lessons applied: </span>{applied.map((l) => l.text).join(" · ")}</p>
      )}
      {cited.length > 0 && (
        <p className="mt-2 break-all text-xs text-ink-2">Sources: {cited.map((u) => (
          <a key={u} href={u} target="_blank" rel="noreferrer" className="mr-2 text-s1">{new URL(u).hostname}</a>
        ))}</p>
      )}
    </Card>
  );
}
