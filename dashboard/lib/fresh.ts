/** "Data as of" and staleness: amber after 2x the producing job's interval, red after 6x.
 * Market-hours jobs are not flagged outside 09:15-15:30 IST on weekdays. */
export type Freshness = "fresh" | "amber" | "red" | "unknown";

export interface JobSpec {
  job: string;
  everyMin: number;
  marketOnly?: boolean;
}

const IST_OFFSET_MIN = 330;

export function istParts(d: Date): { day: number; minutes: number } {
  const t = new Date(d.getTime() + IST_OFFSET_MIN * 60_000);
  return { day: t.getUTCDay(), minutes: t.getUTCHours() * 60 + t.getUTCMinutes() };
}

export function marketOpen(d: Date): boolean {
  const { day, minutes } = istParts(d);
  return day >= 1 && day <= 5 && minutes >= 9 * 60 + 15 && minutes <= 15 * 60 + 30;
}

export function freshness(last: Date | null, spec: JobSpec, now: Date): Freshness {
  if (!last) return "unknown";
  const ageMin = (now.getTime() - last.getTime()) / 60_000;
  if (spec.marketOnly && !marketOpen(now)) return "fresh";
  if (ageMin > spec.everyMin * 6) return "red";
  if (ageMin > spec.everyMin * 2) return "amber";
  return "fresh";
}

export function worst(levels: Freshness[]): Freshness {
  const order: Freshness[] = ["red", "amber", "unknown", "fresh"];
  return order.find((l) => levels.includes(l)) ?? "fresh";
}

/** Jobs each page depends on (names match worker/app/scheduler.py). */
export const PAGE_JOBS: Record<string, JobSpec[]> = {
  today: [{ job: "signal_cycle", everyMin: 5 }, { job: "trading_cycle", everyMin: 5, marketOnly: true }],
  signals: [{ job: "signal_cycle", everyMin: 5 }],
  trades: [{ job: "trading_cycle", everyMin: 5, marketOnly: true }],
  portfolio: [{ job: "portfolio_snapshot", everyMin: 15, marketOnly: true }],
  rejected: [{ job: "signal_cycle", everyMin: 5 }],
  news: [{ job: "tag_news", everyMin: 3 }, { job: "news_day", everyMin: 15 }],
  sectors: [{ job: "tag_news", everyMin: 3 }],
  calendar: [{ job: "event_calendar", everyMin: 1440 }],
  macro: [{ job: "macro", everyMin: 15 }],
  graph: [{ job: "kg_build", everyMin: 1440 }],
  performance: [{ job: "trading_cycle", everyMin: 5, marketOnly: true }],
  calibration: [{ job: "calibration_resolve", everyMin: 1440 }],
  backtests: [{ job: "backtest_auto", everyMin: 1440 }],
  events: [{ job: "history_weekly", everyMin: 10080 }],
  digest: [{ job: "weekly_digest", everyMin: 10080 }],
  golive: [{ job: "golive_checks", everyMin: 10 }],
  health: [{ job: "heartbeat", everyMin: 5 }],
  reports: [{ job: "monthly_report", everyMin: 44640 }],
  suggestions: [{ job: "learning_suggest", everyMin: 44640 }],
  aivsrules: [{ job: "ai_vs_rules", everyMin: 1440 }],
  quality: [{ job: "data_quality", everyMin: 1440 }],
};
