import type { AsOf } from "@/lib/queries";
import { ago, when } from "@/lib/format";
import { cn } from "@/lib/cn";

/** "Data as of <time>" on every page; amber / red when the producing job is late. */
export function AsOfBar({ asOf, now }: { asOf: AsOf; now: Date }) {
  const late = asOf.jobs.filter((j) => j.level === "amber" || j.level === "red" || j.level === "unknown");
  const tone = asOf.level;
  return (
    <div
      data-testid="asof"
      data-level={tone}
      className={cn(
        "mb-4 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg px-3 py-2 text-xs",
        tone === "fresh" && "bg-grid/50 text-ink-2",
        tone === "amber" && "bg-warn-wash text-ink",
        tone === "red" && "bg-bad-wash text-bad",
        tone === "unknown" && "bg-grid text-ink-2",
      )}
    >
      <span aria-hidden>{tone === "fresh" ? "●" : tone === "unknown" ? "○" : "▲"}</span>
      <span>
        Data as of {asOf.ts ? `${when(asOf.ts)} (${ago(asOf.ts, now)})` : "unknown"}
        {tone === "amber" && " · stale"}
        {tone === "red" && " · very stale"}
      </span>
      {late.length > 0 && <span className="text-ink-2">· late: {late.map((j) => `${j.job} (${ago(j.last, now)})`).join(", ")}</span>}
    </div>
  );
}
