import Link from "next/link";
import { Card, PageTitle } from "@/components/ui";

const PAGES: [string, string, string][] = [
  ["/portfolio", "Portfolio", "Equity, drawdown, capital used, exposure"],
  ["/rejected", "Rejected", "Rejected candidates and their counterfactuals"],
  ["/news", "News feed", "Tagged news and structured events"],
  ["/sectors", "Sector heatmap", "Sentiment and signals per sector"],
  ["/calendar", "Calendar", "Results, holidays, expiries, macro"],
  ["/macro", "Macro", "Crude, INR, VIX, flows, commodities, ADRs"],
  ["/graph", "Knowledge graph", "Links between stocks and propagations"],
  ["/performance", "Performance", "Win rate, expectancy, breakdowns"],
  ["/calibration", "Calibration", "Scorecard and reliability"],
  ["/backtests", "Backtests", "Variants, verdict, equity"],
  ["/events", "Event library", "Historical reactions and analogues"],
  ["/digest", "Weekly digest", "Sunday review and suggestions"],
  ["/reports", "Monthly reports", "What worked, what didn't, PDF on Telegram"],
  ["/research", "Research agent", "Research files, lessons, runs and cost"],
  ["/suggestions", "Suggestions", "Learning-loop changes to accept or reject"],
  ["/ai-vs-rules", "AI vs rules", "AI decisions vs the rules-only path"],
  ["/quality", "Data quality", "Checks, database size, AI cost"],
  [
    "/watchlist",
    "Watchlist book",
    "Near-misses traded apart from the main book",
  ],
  [
    "/golive",
    "Go-live checks",
    "2-hour check, first-run report, unverified items",
  ],
  ["/health", "System health", "Jobs, sources, AI spend"],
  ["/settings", "Settings", "Owner switches, config, change log"],
];

export default function More() {
  return (
    <>
      <PageTitle title="More" />
      <Card>
        {PAGES.map(([href, title, sub]) => (
          <Link
            key={href}
            href={href}
            className="flex items-center justify-between border-t border-line py-3 first:border-t-0"
          >
            <span>
              <span className="block text-sm font-medium">{title}</span>
              <span className="text-xs text-ink-2">{sub}</span>
            </span>
            <span aria-hidden className="text-muted">
              ›
            </span>
          </Link>
        ))}
      </Card>
    </>
  );
}
