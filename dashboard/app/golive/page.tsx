import { AsOfBar } from "@/components/asof";
import { Badge, Card, Empty, PageTitle } from "@/components/ui";
import { getDb } from "@/lib/data";
import { when } from "@/lib/format";
import { asOf } from "@/lib/queries";

export const dynamic = "force-dynamic";

type Check = {
  name: string;
  status: "ok" | "warn" | "fail";
  summary: string;
  details: string[];
};
type Run = {
  id: number;
  kind: string;
  created_at: string;
  status: Check["status"];
  results_json: { title?: string; checks: Check[] };
};
const TONE = { ok: "good", warn: "warn", fail: "bad" } as const;

export default async function GolivePage() {
  const db = await getDb();
  const [freshness, runs] = await Promise.all([
    asOf(db, "golive"),
    db.rows<Run>("golive_checks", { order: ["created_at", "desc"], limit: 10 }),
  ]);
  return (
    <>
      <PageTitle
        title="Go-live checks"
        sub="Quick check 2 h after the first start (NSE endpoints first), first-run report after 24 h. Also on Telegram."
      />
      <AsOfBar asOf={freshness} now={db.now()} />
      {runs.length === 0 && (
        <Empty>
          No go-live check yet. The quick check runs 2 hours after the worker
          first starts.
        </Empty>
      )}
      <div className="space-y-3">
        {runs.map((r) => (
          <Card
            key={r.id}
            title={`${r.kind === "full" ? "First-run report (24 h)" : r.kind === "quick" ? "Quick check (2 h)" : (r.results_json.title ?? "Manual check")} · ${when(r.created_at)}`}
            action={
              <Badge tone={TONE[r.status]}>{r.status.toUpperCase()}</Badge>
            }
          >
            <ul className="space-y-2 text-sm">
              {r.results_json.checks.map((c) => (
                <li key={c.name}>
                  <div className="flex items-start gap-2">
                    <Badge tone={TONE[c.status]}>{c.name}</Badge>
                    <span>{c.summary}</span>
                  </div>
                  {c.details.length > 0 && (
                    <ul className="mt-1 ml-4 list-disc text-xs text-ink-2">
                      {c.details.map((d, i) => (
                        <li key={i}>{d}</li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </ul>
          </Card>
        ))}
      </div>
    </>
  );
}
