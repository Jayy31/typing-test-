import { AsOfBar } from "@/components/asof";
import { Reliability } from "@/components/charts";
import { Badge, Card, PageTitle, Stat, Table } from "@/components/ui";
import { getDb } from "@/lib/data";
import { predictor, when } from "@/lib/format";
import { asOf } from "@/lib/queries";

export const dynamic = "force-dynamic";

interface Score { p_move: number | null; direction_hit: boolean | null; prediction_source: string | null; ai_preset?: string | null; status: string }
interface Calibrator { id: number; target: string; source: string; method: string; version: number; n_samples: number; n_events: number | null; brier: number | null; fitted_at: string; active: boolean }

export default async function Calibration() {
  const db = await getDb();
  const [freshness, scores, cals] = await Promise.all([
    asOf(db, "calibration"),
    db.rows<Score>("prediction_scores", { eq: { status: "scored" } }),
    db.rows<Calibrator>("calibrators", { eq: { active: true } }),
  ]);
  const pts = scores.filter((s) => s.p_move != null && s.direction_hit != null);
  const bins = [0.4, 0.5, 0.6, 0.7, 0.8].map((lo) => {
    const inBin = pts.filter((s) => (s.p_move as number) >= lo && (s.p_move as number) < lo + 0.1);
    return { p: lo + 0.05, obs: inBin.length ? inBin.filter((s) => s.direction_hit).length / inBin.length : 0, n: inBin.length };
  }).filter((b) => b.n > 0);
  const brier = pts.length ? pts.reduce((a, s) => a + ((s.p_move as number) - (s.direction_hit ? 1 : 0)) ** 2, 0) / pts.length : null;
  return (
    <>
      <PageTitle title="Prediction scorecard and calibration" />
      <AsOfBar asOf={freshness} now={db.now()} />
      {cals.length === 0 && (
        <div className="mb-3 rounded-lg bg-warn-wash px-3 py-2 text-sm" data-testid="not-calibrated">
          ▲ Not yet calibrated: probabilities are shrunk toward the option-implied market until a calibrator is fitted (Platt from 50, isotonic from 200 independent events).
        </div>
      )}
      <div className="grid grid-cols-2 gap-3">
        <Stat label="Scored predictions" value={pts.length} sub={`${pts.filter((s) => s.prediction_source === "agent").length} agent · ${pts.filter((s) => s.prediction_source === "panel").length} panel · ${pts.filter((s) => !["agent", "panel"].includes(s.prediction_source ?? "")).length} single`} />
        <Stat label="Brier score (raw p_move)" value={brier?.toFixed(3) ?? "n/a"} sub="lower is better; 0.25 = coin flip" />
      </div>
      <Card title="By prediction source and preset" className="mt-3">
        <Table head={["Source", "Preset", "Scored", "Direction hit", "Brier"]} rows={bySource(pts).map((g) => [
          predictor(g.source), g.preset ?? "-", g.n, `${Math.round(g.hit * 100)}%`, g.brier.toFixed(3),
        ])} />
      </Card>
      <Card title="Reliability (raw p_move vs direction hit)" className="mt-3">
        <Reliability data={bins} />
        <Table className="mt-2" head={["Predicted", "Observed", "n"]} rows={bins.map((b) => [`${Math.round(b.p * 100)}%`, `${Math.round(b.obs * 100)}%`, b.n])} />
      </Card>
      <Card title="Active calibrators" className="mt-3">
        {cals.length === 0 ? <p className="text-sm text-muted">None yet.</p> : (
          <Table head={["Target", "Source", "Method", "Events", "Brier", "Fitted"]} rows={cals.map((c) => [
            c.target, c.source, <Badge key="m" tone="good">{c.method} v{c.version}</Badge>, c.n_events ?? c.n_samples, c.brier?.toFixed(3) ?? "n/a", when(c.fitted_at),
          ])} />
        )}
      </Card>
    </>
  );
}

function bySource(pts: Score[]) {
  const m = new Map<string, Score[]>();
  pts.forEach((p) => {
    const k = `${p.prediction_source ?? "single"}|${p.ai_preset ?? ""}`;
    m.set(k, [...(m.get(k) ?? []), p]);
  });
  return [...m].map(([k, v]) => {
    const [source, preset] = k.split("|");
    return {
      source, preset: preset || null, n: v.length,
      hit: v.filter((s) => s.direction_hit).length / v.length,
      brier: v.reduce((a, s) => a + ((s.p_move as number) - (s.direction_hit ? 1 : 0)) ** 2, 0) / v.length,
    };
  }).sort((a, b) => b.n - a.n);
}
