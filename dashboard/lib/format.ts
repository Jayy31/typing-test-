export function inr(x: number | null | undefined, signed = false): string {
  if (x == null || Number.isNaN(x)) return "n/a";
  const a = Math.abs(x);
  const s = a.toLocaleString("en-IN", { maximumFractionDigits: a > 0 && a < 100 ? 1 : 0 });
  return `${x < 0 ? "-" : signed && x > 0 ? "+" : ""}₹${s}`;
}

export function pct(x: number | null | undefined, signed = true, digits = 1): string {
  if (x == null || Number.isNaN(x)) return "n/a";
  return `${signed && x > 0 ? "+" : ""}${x.toFixed(digits)}%`;
}

export function prob(x: number | null | undefined): string {
  return x == null ? "n/a" : `${Math.round(x * 100)}%`;
}

const fmt = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
const fmtDay = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric" });

export function when(ts: string | Date | null | undefined): string {
  if (!ts) return "n/a";
  return fmt.format(typeof ts === "string" ? new Date(ts) : ts);
}

export function day(ts: string | Date | null | undefined): string {
  if (!ts) return "n/a";
  return fmtDay.format(typeof ts === "string" ? new Date(ts) : ts);
}

export function ago(ts: string | Date | null | undefined, now: Date): string {
  if (!ts) return "never";
  const min = Math.round((now.getTime() - new Date(ts).getTime()) / 60_000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  if (min < 48 * 60) return `${Math.round(min / 60)} h ago`;
  return `${Math.round(min / 1440)} days ago`;
}

export const SOURCE_LABEL: Record<string, string> = {
  scenario_tree: "scenario tree",
  analogues: "historical analogues",
  blended: "blended (scenario tree + analogues)",
};

export const ORDER_LABEL: Record<number, string> = {
  1: "direct",
  2: "via graph, 1 hop",
  3: "via graph, 2 hops",
};

/** Calibration label: required wording until a calibrator exists. */
export function calibrationLabel(calibratorId: unknown, method: unknown): string {
  return calibratorId && method && method !== "shrink" ? `calibrated (${String(method)})` : "not yet calibrated";
}

/** Short Indian-style rupees for chart axes: ₹10.8L, ₹1.2Cr, ₹45k. */
export function inrShort(x: number): string {
  const a = Math.abs(x), s = x < 0 ? "-" : "";
  const f = (v: number) => (v >= 100 ? v.toFixed(0) : v.toFixed(v >= 10 ? 1 : 2).replace(/\.?0+$/, ""));
  if (a >= 1e7) return `${s}₹${f(a / 1e7)}Cr`;
  if (a >= 1e5) return `${s}₹${f(a / 1e5)}L`;
  if (a >= 1e3) return `${s}₹${f(a / 1e3)}k`;
  return `${s}₹${Math.round(a)}`;
}
