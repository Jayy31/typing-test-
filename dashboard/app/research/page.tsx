import Link from "next/link";
import { AsOfBar } from "@/components/asof";
import { Badge, Card, Empty, KV, PageTitle, Stat, Table } from "@/components/ui";
import { getDb } from "@/lib/data";
import { day, inr, when } from "@/lib/format";
import { asOf, istDayStart } from "@/lib/queries";
import { deleteLesson } from "./actions";

export const dynamic = "force-dynamic";

interface Run {
  id: number; kind: string; role: string; preset: string; model: string; effort: string | null; symbol: string | null;
  subject: string | null; analysis_id: number | null; status: string; limited_reason: string | null; tool_calls: number;
  web_searches: number; web_fetches: number; input_tokens: number; output_tokens: number; cost_inr: number;
  estimate_inr: number | null; duration_s: number | null; created_at: string; error?: string | null;
}
interface FileRow {
  id: number; subject_kind: string; subject: string; version: number; model: string | null; preset: string | null;
  cost_inr: number | null; created_at: string;
  content_json: {
    summary?: string; thesis?: string; risks?: string[]; changes_since_last?: string | null;
    catalysts?: { date: string; event: string; why_it_matters: string }[];
    sensitivities?: { driver: string; stock_move_pct: string; basis: string }[];
    key_numbers?: { name: string; value: string; source: string }[];
    sector_and_graph_links?: { symbol: string; relation: string }[];
  };
}
interface Lesson {
  id: number; scope_kind: string; scope: string; text: string; week: string | null; deleted: boolean;
  deleted_at: string | null; deleted_by: string | null; evidence_json: { prediction_ids?: number[] } | null; created_at: string;
}

const TONE = { ok: "good", limited: "warn", failed: "bad", pending: "info", running: "info" } as const;
const KIND = { live: "Live", files: "Research file", review: "Weekly review", smoke: "Smoke test" } as Record<string, string>;

export default async function Research({ searchParams }: { searchParams: Promise<{ file?: string }> }) {
  const { file } = await searchParams;
  const db = await getDb();
  const now = db.now();
  const monthStart = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1)).toISOString();
  const [freshness, runs, files, lessons] = await Promise.all([
    asOf(db, "research"),
    db.rows<Run>("research_runs", { order: ["created_at", "desc"], limit: 200 }),
    db.rows<FileRow>("research_files", { order: ["created_at", "desc"], limit: 300 }),
    db.rows<Lesson>("research_lessons", { order: ["created_at", "desc"], limit: 200 }),
  ]);
  const today = istDayStart(now);
  const spend = (kinds: string[], since: string) =>
    runs.filter((r) => kinds.includes(r.kind) && r.created_at >= since).reduce((a, r) => a + r.cost_inr, 0);
  const latest = new Map<string, FileRow>();
  files.forEach((f) => {
    const k = `${f.subject_kind}:${f.subject}`;
    if (!latest.has(k) || latest.get(k)!.version < f.version) latest.set(k, f);
  });
  const open = file ? files.find((f) => String(f.id) === file) : undefined;
  const live = lessons.filter((l) => !l.deleted);
  const gone = lessons.filter((l) => l.deleted);
  return (
    <>
      <PageTitle title="Research agent" sub="Tool-using investigations, overnight research files and weekly lessons. Trade decisions stay in code: the agent only writes the scenario tree." />
      <AsOfBar asOf={freshness} now={now} />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Live runs today" value={runs.filter((r) => r.kind === "live" && r.created_at >= today).length} sub={`${inr(spend(["live"], today))} today`} />
        <Stat label="Live spend this month" value={inr(spend(["live"], monthStart))} sub="cap ₹60/day, ₹1,300/month" />
        <Stat label="Research files this month" value={inr(spend(["files"], monthStart))} sub="cap ₹20/day, ₹400/month" />
        <Stat label="Lessons" value={live.length} sub={`${gone.length} deleted`} />
      </div>

      {open && <FileCard f={open} versions={files.filter((x) => x.subject === open.subject && x.subject_kind === open.subject_kind)} />}

      <Card title="Research files (latest version)" className="mt-3">
        {latest.size === 0 ? <Empty>No research files yet.</Empty> : (
          <Table head={["Subject", "Version", "Updated", "Model", "Summary"]} rows={[...latest.values()].map((f) => [
            <Link key="s" href={`/research?file=${f.id}`} className="text-s1">{f.subject_kind === "theme" ? `theme: ${f.subject}` : f.subject}</Link>,
            `v${f.version}`, when(f.created_at), <span key="m" className="text-xs">{f.model}</span>,
            <span key="x" className="text-xs">{f.content_json.summary ?? ""}</span>,
          ])} />
        )}
      </Card>

      <Card title="Lessons from the weekly self-review" className="mt-3">
        {live.length === 0 ? <Empty>No lessons yet (the review needs resolved predictions made after the AI knowledge cutoff).</Empty> : (
          <ul className="space-y-3">
            {live.map((l) => (
              <li key={l.id} className="flex items-start justify-between gap-3 border-t border-line pt-3 first:border-t-0 first:pt-0" data-lesson={l.id}>
                <div>
                  <Badge>{l.scope_kind}: {l.scope}</Badge>
                  <p className="mt-1 text-sm">{l.text}</p>
                  <p className="text-xs text-ink-2">Week of {l.week ? day(l.week) : "n/a"} · based on {l.evidence_json?.prediction_ids?.length ?? 0} prediction(s)</p>
                </div>
                <form action={deleteLesson}>
                  <input type="hidden" name="id" value={l.id} />
                  <button className="rounded-lg bg-grid px-3 py-1.5 text-xs font-medium text-ink" data-delete-lesson={l.id}>Delete</button>
                </form>
              </li>
            ))}
          </ul>
        )}
        {gone.length > 0 && (
          <p className="mt-3 text-xs text-ink-2">Deleted: {gone.map((l) => `#${l.id} by ${l.deleted_by} (${when(l.deleted_at)})`).join("; ")} — also in Settings → Change log.</p>
        )}
      </Card>

      <Card title="Recent runs" className="mt-3">
        {runs.length === 0 ? <Empty>No runs yet. Switch on “Research agent” in Settings.</Empty> : (
          <Table head={["When", "Kind", "Subject", "Model", "Status", "Tools", "Cost"]} rows={runs.slice(0, 30).map((r) => [
            when(r.created_at), KIND[r.kind] ?? r.kind,
            r.analysis_id && r.symbol ? <span key="s">{r.symbol}</span> : (r.symbol ?? r.subject ?? ""),
            <span key="m" className="text-xs">{r.model} · {r.preset}</span>,
            <Badge key="b" tone={TONE[r.status as keyof typeof TONE] ?? "neutral"}>{r.status}{r.limited_reason ? ` (${r.limited_reason})` : ""}</Badge>,
            `${r.tool_calls}${r.web_searches ? ` + ${r.web_searches} search` : ""}${r.web_fetches ? ` + ${r.web_fetches} fetch` : ""}`,
            <span key="c" className="tnum">{inr(r.cost_inr)}{r.estimate_inr ? <span className="text-xs text-ink-2"> / est {inr(r.estimate_inr)}</span> : null}</span>,
          ])} />
        )}
        {runs.some((r) => r.status === "failed") && (
          <p className="mt-2 text-xs text-ink-2">Failed live runs fall back to the single analyst. Latest failure: {runs.find((r) => r.status === "failed")?.error ?? "n/a"}</p>
        )}
      </Card>
    </>
  );
}

function FileCard({ f, versions }: { f: FileRow; versions: FileRow[] }) {
  const c = f.content_json;
  return (
    <Card title={`Research file: ${f.subject} v${f.version}`} className="mt-3" action={<Badge tone="info">{f.model}</Badge>}>
      <p className="text-sm font-medium">{c.summary}</p>
      {c.thesis && <p className="mt-1 text-sm">{c.thesis}</p>}
      <KV items={[
        ["Updated", `${when(f.created_at)} · preset ${f.preset} · ${inr(f.cost_inr)}`],
        ["Catalysts", (c.catalysts ?? []).map((x) => `${x.date}: ${x.event}`).join("; ") || "none"],
        ["Key numbers", (c.key_numbers ?? []).map((x) => `${x.name} ${x.value}`).join("; ") || "none"],
        ["Links", (c.sector_and_graph_links ?? []).map((x) => `${x.symbol} (${x.relation})`).join(", ") || "none"],
        ["Risks", (c.risks ?? []).join("; ") || "none"],
      ]} />
      {(c.sensitivities ?? []).length > 0 && (
        <Table className="mt-2" head={["What moves it", "Stock move", "Basis"]} rows={(c.sensitivities ?? []).map((x) => [x.driver, x.stock_move_pct, x.basis])} />
      )}
      {c.changes_since_last && <p className="mt-2 text-xs text-ink-2">Changed since last version: {c.changes_since_last}</p>}
      <p className="mt-2 text-xs text-ink-2">Versions: {versions.map((v) => (
        <Link key={v.id} href={`/research?file=${v.id}`} className="mr-2 text-s1">v{v.version}</Link>
      ))}</p>
    </Card>
  );
}
