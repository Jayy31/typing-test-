import { expect, test } from "@playwright/test";

const SHOTS = "e2e/screenshots";

const PAGES: [string, string, string[]][] = [
  [
    "/",
    "01-today",
    [
      "Live signals",
      "Market regime",
      "Inputs: VIX+Nifty+FII",
      "Open trades",
      "Today's events and results",
      "Needs review",
      "System health",
      "AI spend today",
    ],
  ],
  [
    "/signals",
    "02-signals",
    ["not yet calibrated", "via graph, 1 hop", "Enters after the open"],
  ],
  [
    "/signals/1042",
    "03-signal-detail",
    [
      "not yet calibrated",
      "blended (scenario tree + analogues)",
      "Option-market positioning",
      "support only",
      "Panel debate",
      "filing · hand-set rule (low weight)",
      "Candidate strikes",
      "Timeline",
    ],
  ],
  [
    "/signals/1043",
    "04-signal-graph",
    [
      "graph · event-study base rate",
      "INFY › competitor_of › TCS",
      "sign unknown · support only",
    ],
  ],
  ["/trades", "05-trades-open", ["TATASTEEL 162 CE", "fillable", "partial"]],
  ["/trades?tab=closed", "06-trades-closed", ["unfillable", "TRAIL"]],
  [
    "/trades/205",
    "07-trade-detail",
    ["Premium path", "P&L attribution", "Fills"],
  ],
  ["/portfolio", "08-portfolio", ["Equity", "Drawdown", "Exposure by theme"]],
  ["/rejected", "09-rejected", ["By stage", "Would have won", "kg_cap"]],
  [
    "/news",
    "10-news",
    ["graph · event-study base rate", "filing · hand-set rule (low weight)"],
  ],
  ["/sectors", "11-sectors", ["Telecom"]],
  [
    "/calendar",
    "12-calendar",
    ["Today · ", "results", "Recent macro releases"],
  ],
  ["/macro", "13-macro", ["Brent", "Commodities", "Indian ADRs"]],
  [
    "/graph?symbol=MOTHERSON",
    "14-graph",
    ["supplier of", "25% revenue share", "Recent propagations"],
  ],
  [
    "/performance",
    "15-performance",
    ["Win rate", "By impact order", "By prediction"],
  ],
  ["/calibration", "16-calibration", ["Not yet calibrated", "Reliability"]],
  [
    "/backtests",
    "17-backtests",
    ["R1:", "knowledge graph (3 hops)", "Verdict"],
  ],
  [
    "/events",
    "18-events",
    [
      "Direct events",
      "Linked stocks (knowledge graph)",
      "kg:competitor_of:order_win",
    ],
  ],
  ["/digest", "19-digest", ["REGULATOR ITEMS TO REVIEW"]],
  ["/health", "20-health", ["Sources", "nse_insider", "AI smoke test"]],
  ["/more", "21-more", ["Event library", "Settings"]],
  [
    "/settings",
    "22-settings",
    ["Owner switches", "Change log", "worker / Telegram"],
  ],
  ["/reports", "23-reports", ["September 2026"]],
  [
    "/reports/2026-09",
    "24-report",
    ["every figure checked against the report", "What worked", "results_beat"],
  ],
  [
    "/suggestions",
    "25-suggestions",
    ["edge_ratio_min", "97.5% interval", "Accept (opens a PR)", "Pull request"],
  ],
  ["/ai-vs-rules", "26-ai-vs-rules", ["AI signals", "Rules (analogues only)"]],
  ["/quality", "27-quality", ["bid above ask", "Database", "Cache hits"]],
  [
    "/watchlist",
    "30-watchlist",
    ["Main book vs watchlist book", "Edge ratio", "missed: Min EV %"],
  ],
  [
    "/stocks/BHARTIARTL",
    "29-stock",
    ["Option-market positioning", "long buildup", "S-1042", "Paper trades"],
  ],
  [
    "/golive",
    "28-golive",
    ["First-run report", "BLOCKED", "alternative: BSE insider", "needs you"],
  ],
];

for (const [path, name, texts] of PAGES) {
  test(`${name} ${path}`, async ({ page }) => {
    const res = await page.goto(path);
    expect(res?.status()).toBe(200);
    for (const t of texts)
      await expect(page.getByText(t, { exact: false }).first()).toBeVisible();
    if (!["/more", "/settings", "/reports/2026-09"].includes(path))
      await expect(page.getByTestId("asof")).toContainText("Data as of");
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth + 1,
    );
    expect(overflow, "no horizontal page scroll on mobile").toBe(false);
    // full-page capture: show the fixed bottom nav once, at the end, instead of over the content
    await page.addStyleTag({
      content: "nav[aria-label=Main]{position:static!important}",
    });
    await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
  });
}

test("stale data is highlighted", async ({ page }) => {
  await page.goto("/macro");
  await expect(page.getByTestId("asof")).toHaveAttribute("data-level", "amber");
  await page.goto("/signals");
  await expect(page.getByTestId("asof")).toHaveAttribute("data-level", "fresh");
});

test("owner switch change is logged", async ({ page }) => {
  await page.goto("/settings");
  const sw = page.locator('button[data-key="brief.evening_enabled"]');
  await expect(sw).toHaveAttribute("aria-checked", "true");
  await sw.click();
  await expect(
    page.locator('button[data-key="brief.evening_enabled"]'),
  ).toHaveAttribute("aria-checked", "false");
  await expect(page.getByText("brief.evening_enabled").nth(1)).toBeVisible();
  await expect(page.getByText("true → false").first()).toBeVisible();
  await page.locator('button[data-key="brief.evening_enabled"]').click(); // restore for other runs
});

test("suggestion decision is recorded", async ({ page }) => {
  await page.goto("/suggestions");
  await page.locator('button[data-action="accepted"][data-id="11"]').click();
  await expect(page.getByText("Nothing waiting for a decision.")).toBeVisible();
  await expect(page.getByText("by owner@example.com").first()).toBeVisible();
});

test("theme switch: auto -> light -> dark, remembered after reload", async ({
  page,
}) => {
  await page.goto("/");
  const t = page.getByTestId("theme-toggle");
  await expect(t).toContainText("Auto");
  await t.click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await t.click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.getByTestId("theme-toggle")).toContainText("Dark");
  const bg = await page.evaluate(
    () => getComputedStyle(document.body).backgroundColor,
  );
  expect(bg).toBe("rgb(13, 13, 13)");
  for (const [path, name] of [
    ["/", "dark-01-today"],
    ["/signals/1042", "dark-03-signal-detail"],
    ["/trades/205", "dark-07-trade-detail"],
    ["/portfolio", "dark-08-portfolio"],
    ["/performance", "dark-15-performance"],
    ["/settings", "dark-22-settings"],
  ]) {
    await page.goto(path);
    await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
  }
});
