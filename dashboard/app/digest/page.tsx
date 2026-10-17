import { AsOfBar } from "@/components/asof";
import { Card, PageTitle } from "@/components/ui";
import { getDb } from "@/lib/data";
import { day } from "@/lib/format";
import { asOf } from "@/lib/queries";

export const dynamic = "force-dynamic";

export default async function DigestPage() {
  const db = await getDb();
  const [freshness, rows] = await Promise.all([
    asOf(db, "digest"),
    db.rows<{ id: number; week_start: string; text: string; created_at: string }>("digests", { order: ["week_start", "desc"], limit: 12 }),
  ]);
  return (
    <>
      <PageTitle title="Weekly digest" sub="Every Sunday. Suggestions only: nothing is changed automatically." />
      <AsOfBar asOf={freshness} now={db.now()} />
      <div className="space-y-3">
        {rows.map((d) => (
          <Card key={d.id} title={`Week of ${day(d.week_start)}`}>
            <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed">{d.text}</pre>
          </Card>
        ))}
      </div>
    </>
  );
}
