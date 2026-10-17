import { AsOfBar } from "@/components/asof";
import { Badge, Card, PageTitle, Stat, Table } from "@/components/ui";
import { getDb } from "@/lib/data";
import { ago, inr, when } from "@/lib/format";
import { PAGE_JOBS } from "@/lib/fresh";
import { asOf, config, istDayStart, systemHealth } from "@/lib/queries";
import type { JobRun } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function Health() {
  const db = await getDb();
  const now = db.now();
  const [freshness, health, cfg, runs, smoke, usage] = await Promise.all([
    asOf(db, "health"), systemHealth(db), config(db),
    db.rows<JobRun>("job_runs", { order: ["started_at", "desc"], limit: 300 }),
    db.rows<{ passed: boolean; total_cost_inr: number; panel_seconds: number | null; started_at: string }>("ai_smoke_runs", { order: ["started_at", "desc"], limit: 1 }),
    db.rows<{ ts: string; cost_inr: number }>("ai_usage", { gte: ["ts", new Date(now.getTime() - 30 * 86_400_000).toISOString()] }),
  ]);
  const latest = new Map<string, JobRun>();
  runs.forEach((r) => { if (!latest.has(r.job)) latest.set(r.job, r); });
  const errors = runs.filter((r) => r.status === "error").slice(0, 8);
  const sources = Object.entries((cfg.source_health ?? {}) as Record<string, { last_ok?: string; last_error?: string; consecutive_failures?: number }>);
  const month = usage.reduce((a, u) => a + u.cost_inr, 0);
  const today = usage.filter((u) => u.ts >= istDayStart(now)).reduce((a, u) => a + u.cost_inr, 0);
  const pageJobs = new Set(Object.values(PAGE_JOBS).flat().map((j) => j.job));
  return (
    <>
      <PageTitle title="System health" />
      <AsOfBar asOf={freshness} now={now} />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Status" value={<Badge tone={health.level === "green" ? "good" : health.level === "amber" ? "warn" : "bad"}>{health.level}</Badge>} />
        <Stat label="AI today" value={inr(today)} />
        <Stat label="AI last 30 days" value={inr(month)} />
        <Stat label="AI smoke test" value={smoke[0] ? (smoke[0].passed ? "passed" : "failed") : "not run"} sub={smoke[0] ? `${when(smoke[0].started_at)} · ${inr(smoke[0].total_cost_inr)}` : undefined} />
      </div>
      {health.problems.length > 0 && (
        <Card title="Problems" className="mt-3">
          {health.problems.map((p) => <p key={p} className="border-t border-line py-2 text-sm text-bad first:border-t-0">▲ {p}</p>)}
        </Card>
      )}
      <Card title="Sources" className="mt-3">
        <Table head={["Source", "Last OK", "Failures", "Last error"]} rows={sources.map(([k, v]) => [
          k, ago(v.last_ok, now), (v.consecutive_failures ?? 0) >= 3 ? <Badge key="f" tone="bad">{v.consecutive_failures}</Badge> : (v.consecutive_failures ?? 0),
          <span key="e" className="text-xs text-ink-2">{v.last_error ?? ""}</span>,
        ])} />
      </Card>
      <Card title="Jobs (latest run)" className="mt-3">
        <Table head={["Job", "Last run", "Status"]} rows={[...latest.values()].sort((a, b) => a.job.localeCompare(b.job)).map((r) => [
          <span key="j" className={pageJobs.has(r.job) ? "font-medium" : ""}>{r.job}</span>, ago(r.finished_at ?? r.started_at, now),
          <Badge key="s" tone={r.status === "ok" ? "good" : "bad"}>{r.status}</Badge>,
        ])} />
      </Card>
      <Card title="Recent errors" className="mt-3">
        {errors.length === 0 ? <p className="text-sm text-muted">None.</p> : errors.map((r) => (
          <p key={r.id} className="border-t border-line py-2 text-sm first:border-t-0">{r.job} · {when(r.started_at)}<span className="block text-xs text-ink-2">{r.error}</span></p>
        ))}
      </Card>
    </>
  );
}
