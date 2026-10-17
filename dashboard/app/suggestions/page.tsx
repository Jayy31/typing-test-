import { AsOfBar } from "@/components/asof";
import { Badge, Card, Empty, KV, PageTitle } from "@/components/ui";
import { getDb } from "@/lib/data";
import { day, when } from "@/lib/format";
import { asOf } from "@/lib/queries";
import { decide } from "./actions";

export const dynamic = "force-dynamic";

interface Suggestion {
  id: number; month: string; kind: string; param: string; file: string; yaml_path: string; current_value: number; proposed_value: number;
  expected_effect: number; status: string; decided_at: string | null; decided_by: string | null; pr_url: string | null; applied_at: string | null;
  review_json: { actual_effect?: number; predicted_effect?: number; helped?: boolean } | null;
  evidence_json: { n?: number; mean_diff_pct?: number; ci?: [number, number]; ci_level?: number; halves?: number[]; stress_mean_diff_pct?: number; reason?: string; actual_effect?: number; predicted_effect?: number };
}

const TONE = { pending: "warn", accepted: "info", applied: "good", rejected: "neutral", superseded: "neutral" } as const;

export default async function Suggestions() {
  const db = await getDb();
  const [freshness, rows] = await Promise.all([asOf(db, "suggestions"), db.rows<Suggestion>("suggestions", { order: ["created_at", "desc"], limit: 50 })]);
  const pending = rows.filter((r) => r.status === "pending");
  const rest = rows.filter((r) => r.status !== "pending");
  return (
    <>
      <PageTitle title="Learning-loop suggestions" sub="At most 2 a month, each backed by a 97.5% interval, both halves of the period and a 2x-spread check. Accept opens a GitHub PR; one accepted change is merged per month, at month start." />
      <AsOfBar asOf={freshness} now={db.now()} />
      <div className="space-y-3">
        {pending.length === 0 && <Card><Empty>Nothing waiting for a decision.</Empty></Card>}
        {pending.map((g) => <SuggestionCard key={g.id} g={g} />)}
      </div>
      {rest.length > 0 && <h2 className="mt-5 mb-2 text-sm font-semibold">History</h2>}
      <div className="space-y-3">{rest.map((g) => <SuggestionCard key={g.id} g={g} />)}</div>
    </>
  );
}

function SuggestionCard({ g }: { g: Suggestion }) {
  const e = g.evidence_json;
  return (
    <Card title={<span>{g.kind === "revert" ? "Revert: " : ""}<code className="text-sm">{g.yaml_path}</code></span>}
      action={<Badge tone={TONE[g.status as keyof typeof TONE] ?? "neutral"}>{g.status}</Badge>}>
      <p className="tnum text-lg font-semibold">{g.current_value} → {g.proposed_value}</p>
      <p className="mb-3 text-xs text-ink-2">{g.file} · {day(g.month)}</p>
      <KV items={[
        ["Expected effect", `${g.expected_effect > 0 ? "+" : ""}${g.expected_effect.toFixed(1)}% of premium / month`],
        ["Opportunities", String(e.n ?? "n/a")],
        [`${((e.ci_level ?? 0.975) * 100).toFixed(1)}% interval`, e.ci ? `${e.ci[0].toFixed(2)} … ${e.ci[1].toFixed(2)}% per opportunity` : "n/a"],
        ["Both halves", e.halves ? e.halves.map((h) => h.toFixed(2)).join(" / ") : "n/a"],
        ["At 2x spread", e.stress_mean_diff_pct != null ? `${e.stress_mean_diff_pct.toFixed(2)}%` : "n/a"],
        ["Decided", g.decided_at ? `${when(g.decided_at)} by ${g.decided_by}` : "-"],
      ]} />
      {e.reason && <p className="mt-2 text-sm">Why: {e.reason} (predicted {e.predicted_effect}, actual {e.actual_effect})</p>}
      {g.review_json && <p className="mt-2 text-sm">Review after 2 months: predicted {g.review_json.predicted_effect}, actual {g.review_json.actual_effect} — {g.review_json.helped ? "helped" : "did not help (revert suggested)"}</p>}
      {g.pr_url && <a href={g.pr_url} className="mt-2 inline-block text-sm text-s1" target="_blank" rel="noreferrer">Pull request</a>}
      {g.status === "pending" && (
        <div className="mt-3 flex gap-2">
          {(["accepted", "rejected"] as const).map((status) => (
            <form key={status} action={decide} className="flex-1">
              <input type="hidden" name="id" value={g.id} />
              <input type="hidden" name="status" value={status} />
              <button className={status === "accepted" ? "w-full rounded-lg bg-s1 py-2 text-sm font-medium text-white" : "w-full rounded-lg bg-grid py-2 text-sm font-medium text-ink"}
                data-action={status} data-id={g.id}>
                {status === "accepted" ? "Accept (opens a PR)" : "Reject"}
              </button>
            </form>
          ))}
        </div>
      )}
    </Card>
  );
}
