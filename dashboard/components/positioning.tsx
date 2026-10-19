import { Badge, Card, Empty } from "@/components/ui";

export type Evidence = {
  feature: string;
  state: string;
  value: number;
  basis: string;
  lean: string | null;
  n: number;
  mean_pct: number | null;
  hit_up: number | null;
  weight: number;
};
export type PositioningJs = {
  as_of: string;
  source: string;
  evidence: Evidence[];
};

const LABEL: Record<string, string> = {
  oi_buildup: "OI build-up",
  pcr: "Put/call ratio (OI)",
  iv_skew: "IV skew",
  term_structure: "IV term structure",
  basis: "Futures basis",
  rollover: "Rollover",
  unusual_activity: "Unusual option activity",
  delivery: "Delivery %",
};
export const featureLabel = (f: string) => LABEL[f] ?? f;

/** Freshness label: previous-session closing data must never read as live intraday data. */
export const freshnessLabel = (source: string, asOf: string) =>
  source === "eod"
    ? `EOD, previous session (${asOf})`
    : `live intraday (${asOf})`;

/** Positioning evidence for a signal: support-only states change nothing; base-rate states tilt the prediction. */
export function PositioningCard({
  p,
  className,
}: {
  p: PositioningJs | null | undefined;
  className?: string;
}) {
  return (
    <Card
      title="Option-market positioning"
      className={className}
      action={
        p ? (
          <span className="text-xs text-muted">
            {freshnessLabel(p.source, p.as_of)}
          </span>
        ) : undefined
      }
    >
      {!p?.evidence?.length && (
        <Empty>No positioning data for this signal.</Empty>
      )}
      <ul className="space-y-1.5 text-sm">
        {(p?.evidence ?? []).map((e) => (
          <li key={e.feature} className="flex flex-wrap items-center gap-1.5">
            <span className="text-ink-2">{featureLabel(e.feature)}:</span>
            <span className="font-medium">{e.state.replaceAll("_", " ")}</span>
            {e.basis === "base_rate" ? (
              <Badge tone="info">
                base rate n={e.n} ·{" "}
                {e.mean_pct != null
                  ? `${e.mean_pct > 0 ? "+" : ""}${e.mean_pct.toFixed(2)}%`
                  : ""}{" "}
                · weight {Math.round(e.weight * 100)}%
              </Badge>
            ) : (
              <Badge>
                support only (n={e.n}){e.lean ? ` · leans ${e.lean}` : ""}
              </Badge>
            )}
          </li>
        ))}
      </ul>
      {p?.evidence?.length ? (
        <p className="mt-2 text-xs text-muted">
          Evidence only, never a signal on its own. Support-only states change
          nothing.
        </p>
      ) : null}
    </Card>
  );
}
