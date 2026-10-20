/**
 * In-memory sample data (DASHBOARD_FIXTURES=1) shaped exactly like the Postgres tables, for e2e
 * tests and screenshots without a database. Clock frozen at Thu 15-Oct-2026 11:40 IST.
 */
import { applyQuery, type Db, type Query, type Row } from "./db";

export const FIXTURE_NOW = new Date("2026-10-15T06:10:00Z");
const T = (minAgo: number) =>
  new Date(FIXTURE_NOW.getTime() - minAgo * 60_000).toISOString();
const D = (daysAgo: number) => T(daysAgo * 1440);
const dateOnly = (daysFromNow: number) =>
  new Date(FIXTURE_NOW.getTime() + daysFromNow * 86_400_000)
    .toISOString()
    .slice(0, 10);

const instruments: Row[] = [
  ["BHARTIARTL", "Bharti Airtel", "Telecom", "Telecom Services", 475],
  ["TCS", "Tata Consultancy Services", "IT", "IT Services", 175],
  ["INFY", "Infosys", "IT", "IT Services", 400],
  ["WIPRO", "Wipro", "IT", "IT Services", 3000],
  ["SUNPHARMA", "Sun Pharmaceutical Industries", "Pharma", "Pharma", 350],
  ["TATASTEEL", "Tata Steel", "Metals", "Steel", 5500],
  ["MARUTI", "Maruti Suzuki India", "Auto", "Passenger Vehicles", 50],
  [
    "MOTHERSON",
    "Samvardhana Motherson International",
    "Auto",
    "Auto Ancillaries",
    6150,
  ],
  ["HDFCBANK", "HDFC Bank", "Banks", "Private Bank", 550],
  ["SBIN", "State Bank of India", "Banks", "PSU Bank", 750],
].map(([symbol, name, sector, sub_sector, lot_size]) => ({
  symbol,
  name,
  sector,
  sub_sector,
  lot_size,
  is_fno: true,
}));

const signals: Row[] = [
  {
    id: 1042,
    symbol: "BHARTIARTL",
    sector: "Telecom",
    direction: "bullish",
    score: 78,
    status: "ACTIVE",
    strike: 1640,
    expiry: "2026-11-26",
    option_type: "CE",
    pricing_status: "priced",
    expected_value: 18.4,
    expected_value_inr: 2140,
    p_t1_before_sl: 0.61,
    p_move: 0.64,
    predicted_move_pct: 2.9,
    predicted_move_raw_pct: 4.1,
    predicted_move_source: "blended",
    implied_move_pct: 2.1,
    edge_ratio: 1.38,
    edge_ratio_raw: 1.95,
    shrink_weight: 0.5,
    calibrator_id: null,
    calibration_method: "shrink",
    impact_order: 1,
    prediction_source: "panel",
    enter_after: null,
    created_at: T(95),
    updated_at: T(20),
    analysis_id: 501,
    theme_key: "telecom_tariffs",
    engine_version: "8.0.0",
    expected_value_raw: 31.0,
    p_t1_before_sl_raw: 0.68,
    iv_percentile: 41,
    news_to_signal_s: 412,
  },
  {
    id: 1043,
    symbol: "TCS",
    sector: "IT",
    direction: "bullish",
    score: 66,
    status: "NEW",
    strike: 4150,
    expiry: "2026-11-26",
    option_type: "CE",
    pricing_status: "priced",
    expected_value: 12.1,
    expected_value_inr: 1180,
    p_t1_before_sl: 0.57,
    p_move: 0.58,
    predicted_move_pct: 1.9,
    predicted_move_raw_pct: 2.6,
    predicted_move_source: "scenario_tree",
    implied_move_pct: 1.5,
    edge_ratio: 1.27,
    edge_ratio_raw: 1.73,
    shrink_weight: 0.5,
    calibrator_id: null,
    calibration_method: "shrink",
    impact_order: 2,
    prediction_source: "agent",
    ai_preset: "budget",
    enter_after: null,
    created_at: T(35),
    updated_at: T(35),
    analysis_id: 502,
    theme_key: "it_services",
    engine_version: "8.0.0",
  },
  {
    id: 1044,
    symbol: "SUNPHARMA",
    sector: "Pharma",
    direction: "bearish",
    score: 71,
    status: "NEW",
    strike: 1720,
    expiry: "2026-11-26",
    option_type: "PE",
    pricing_status: "priced",
    expected_value: 15.2,
    expected_value_inr: 1630,
    p_t1_before_sl: 0.59,
    p_move: 0.62,
    predicted_move_pct: 3.4,
    predicted_move_raw_pct: 4.8,
    predicted_move_source: "analogues",
    implied_move_pct: 2.4,
    edge_ratio: 1.42,
    shrink_weight: 0.5,
    calibrator_id: null,
    calibration_method: "shrink",
    impact_order: 1,
    prediction_source: "panel",
    enter_after: "2026-10-15T03:45:00Z",
    created_at: D(0.6),
    updated_at: T(80),
    analysis_id: 503,
    theme_key: "pharma_usfda",
    engine_version: "8.0.0",
  },
  {
    id: 1039,
    symbol: "TATASTEEL",
    sector: "Metals",
    direction: "bullish",
    score: 74,
    status: "CONVERTED_TO_TRADE",
    strike: 162,
    expiry: "2026-10-29",
    option_type: "CE",
    pricing_status: "priced",
    expected_value: 21,
    p_t1_before_sl: 0.63,
    predicted_move_pct: 3.1,
    predicted_move_source: "blended",
    calibrator_id: null,
    calibration_method: "shrink",
    impact_order: 1,
    prediction_source: "panel",
    created_at: D(2),
    updated_at: D(2),
    analysis_id: 499,
  },
  {
    id: 1035,
    symbol: "MARUTI",
    sector: "Auto",
    direction: "bullish",
    score: 69,
    status: "CONVERTED_TO_TRADE",
    strike: 12600,
    expiry: "2026-10-29",
    option_type: "CE",
    pricing_status: "priced",
    expected_value: 14,
    p_t1_before_sl: 0.58,
    predicted_move_pct: 2.2,
    predicted_move_source: "scenario_tree",
    calibrator_id: null,
    calibration_method: "shrink",
    impact_order: 1,
    prediction_source: "single",
    created_at: D(5),
    updated_at: D(3),
    analysis_id: 495,
  },
  {
    id: 1030,
    symbol: "HDFCBANK",
    sector: "Banks",
    direction: "bearish",
    score: 64,
    status: "CONVERTED_TO_TRADE",
    strike: 1680,
    expiry: "2026-10-29",
    option_type: "PE",
    pricing_status: "priced",
    predicted_move_pct: 1.8,
    predicted_move_source: "scenario_tree",
    impact_order: 1,
    prediction_source: "single",
    created_at: D(9),
    updated_at: D(6),
    analysis_id: 490,
  },
  {
    id: 1028,
    symbol: "WIPRO",
    sector: "IT",
    direction: "bearish",
    score: 62,
    status: "CONVERTED_TO_TRADE",
    strike: 252,
    expiry: "2026-10-29",
    option_type: "PE",
    predicted_move_pct: 2.0,
    predicted_move_source: "blended",
    impact_order: 2,
    prediction_source: "single",
    created_at: D(12),
    updated_at: D(8),
    analysis_id: 488,
  },
];

const analyses: Row[] = [
  {
    id: 501,
    symbol: "BHARTIARTL",
    direction: "bullish",
    window_days: 5,
    confidence: "high",
    prediction_source: "panel",
    impact_order: 1,
    created_at: T(95),
    model: "judge-model",
    prompt_ver: "judge_v1",
    thesis:
      "Airtel announced a 12-15% tariff hike across prepaid plans effective 3 November. ARPU should rise by about Rs 25 in two quarters with limited churn, as Jio raised prices last week.",
    risks: ["Regulator pushback on tariffs", "Churn to BSNL in rural circles"],
    counter_args: [
      "The hike was partly expected after Jio's move",
      "Stock already up 2% pre-news",
    ],
    analogues: [
      {
        level: "event_type",
        regime: "normal",
        window: 3,
        n: 14,
        n_eff: 12.5,
        mean: 2.4,
        median: 2.1,
        hit_rate: 0.71,
        std: 2.2,
        weight: 0.38,
      },
    ],
    scores_json: {
      regime: "normal/risk_on",
      positioning: {
        as_of: "2026-10-14",
        source: "eod",
        evidence: [
          {
            feature: "oi_buildup",
            state: "long_buildup",
            value: 1.8,
            basis: "base_rate",
            lean: "bullish",
            n: 212,
            mean_pct: 0.62,
            hit_up: 0.57,
            weight: 0.13,
          },
          {
            feature: "pcr",
            state: "high_rising",
            value: 1.42,
            basis: "support",
            lean: null,
            n: 3,
            mean_pct: null,
            hit_up: null,
            weight: 0,
          },
          {
            feature: "delivery",
            state: "high",
            value: 18.5,
            basis: "support",
            lean: null,
            n: 4,
            mean_pct: null,
            hit_up: null,
            weight: 0,
          },
        ],
      },
      cited_news_ids: [9001, 9002, 9003],
      agg_sentiment: 0.71,
      sources: ["nse_announcements", "et_markets", "deep:bulk_deal:rule"],
      has_official: true,
      components: { sentiment: 78, novelty: 70, source_quality: 85, rules: 6 },
      ai_total: 72,
      rules_adjustment: 6,
      priced_in_penalty: 0,
      final: 78,
      panel: {
        bull_case:
          "Tariff hike lifts ARPU; pricing discipline across the industry.",
        bear_case: "Already priced in after Jio; regulatory risk.",
        judge:
          "Bull case stronger: hike is larger than consensus and churn risk is low.",
      },
      options: { pre_news_spot: 1592.0 },
    },
  },
  {
    id: 502,
    symbol: "TCS",
    direction: "bullish",
    window_days: 3,
    confidence: "medium",
    prediction_source: "agent",
    research_run_id: 501,
    ai_preset: "budget",
    ai_models_json: { research_live: "claude-sonnet-5-5" },
    ai_cost_inr: 23.4,
    impact_order: 2,
    created_at: T(35),
    model: "analyst-model",
    prompt_ver: "analyst_v2",
    thesis:
      "Infosys' large deal win with a European bank signals a recovery in BFSI spending; TCS, its closest competitor, is likely to see similar deal momentum.",
    risks: ["Deal is Infosys-specific"],
    counter_args: ["Competitor read-throughs often fade"],
    scores_json: {
      cited_news_ids: [9010],
      agg_sentiment: 0.41,
      sources: ["kg:1:competitor_of:base_rate:propagated"],
      has_official: true,
      final: 66,
    },
  },
  {
    id: 503,
    symbol: "SUNPHARMA",
    direction: "bearish",
    window_days: 5,
    confidence: "high",
    prediction_source: "panel",
    impact_order: 1,
    created_at: D(0.6),
    thesis:
      "USFDA warning letter for the Halol plant (published after the close) restricts new approvals from the site for 12-18 months.",
    risks: ["Halol share of US sales already reduced"],
    counter_args: ["Specialty portfolio offsets"],
    scores_json: {
      cited_news_ids: [9020],
      agg_sentiment: -0.66,
      sources: ["deep:usfda:rule", "nse_announcements"],
      has_official: true,
      final: 71,
    },
  },
];

const scenarios: Row[] = [
  {
    id: 1,
    analysis_id: 501,
    name: "ARPU re-rating",
    probability: 0.55,
    stock_move_pct: 4.5,
    timing_days: 4,
    triggers: ["Brokerage upgrades"],
    invalidation: "Tariff rollback",
  },
  {
    id: 2,
    analysis_id: 501,
    name: "Muted, priced in",
    probability: 0.3,
    stock_move_pct: 0.8,
    timing_days: 3,
    triggers: ["Flat volumes"],
    invalidation: "",
  },
  {
    id: 3,
    analysis_id: 501,
    name: "Regulatory pushback",
    probability: 0.15,
    stock_move_pct: -3.0,
    timing_days: 5,
    triggers: ["TRAI notice"],
    invalidation: "",
  },
  {
    id: 4,
    analysis_id: 502,
    name: "Read-through rally",
    probability: 0.6,
    stock_move_pct: 2.5,
    timing_days: 3,
    triggers: ["Deal commentary"],
    invalidation: "",
  },
  {
    id: 5,
    analysis_id: 502,
    name: "No read-through",
    probability: 0.4,
    stock_move_pct: -0.5,
    timing_days: 3,
    triggers: [],
    invalidation: "",
  },
];

const agent_runs: Row[] = [
  [
    "researcher",
    "Facts: hike 12-15%, effective 3-Nov; Jio hiked 10-12% on 8-Oct; Vi expected to follow.",
    0.08,
  ],
  [
    "novelty",
    "Partly expected: magnitude above street estimates (8-10%).",
    0.04,
  ],
  [
    "bull",
    "ARPU +Rs 25 within two quarters; operating leverage; pricing discipline.",
    2.9,
  ],
  ["bear", "Already up 2% pre-news; regulatory risk; rural churn.", 3.1],
  [
    "judge",
    "Bullish, confidence high: magnitude surprise outweighs the priced-in risk.",
    8.7,
  ],
].map(([agent, summary, cost], i) => ({
  id: 70 + i,
  analysis_id: 501,
  agent,
  model: `${agent}-model`,
  prompt_ver: `${agent}_v1`,
  output_json: { summary },
  input_tokens: 2400,
  output_tokens: 600,
  cost_inr: cost,
  created_at: T(96),
}));

const signal_candidates: Row[] = [
  [1600, 48.2, 49.4, 14.2, 2210, 0.61, false, "lower EV"],
  [1640, 30.1, 30.9, 18.4, 2140, 0.61, true, null],
  [1680, 17.4, 18.2, 16.0, 1520, 0.55, false, "P(T1 before SL) below 0.55"],
  [1720, 9.1, 9.8, 4.2, 410, 0.47, false, "EV below 10% of premium"],
].map(([strike, bid, ask, ev, ev_inr, p, chosen, why], i) => ({
  id: 300 + i,
  signal_id: 1042,
  ts: T(94),
  expiry: "2026-11-26",
  strike,
  option_type: "CE",
  bid,
  ask,
  iv: 22.4,
  oi: 180000,
  volume: 41000,
  spread_pct: 2.6,
  ev_pct: ev,
  ev_inr,
  max_loss_inr: 3600,
  exp_hold_days: 3.2,
  theta_per_day: -1.1,
  breakeven_move_pct: 1.4,
  chosen,
  rejected_reason: why,
  details_json: { p_t1_before_sl: p },
}));

const signal_versions: Row[] = [
  {
    signal_id: 1042,
    version: 1,
    score: 72,
    status: "NEW",
    reason: "Tariff hike announced",
    ts: T(95),
  },
  {
    signal_id: 1042,
    version: 2,
    score: 78,
    status: "ACTIVE",
    reason: "Second source confirmed; bulk deal by MF",
    ts: T(20),
  },
  {
    signal_id: 1043,
    version: 1,
    score: 66,
    status: "NEW",
    reason: "Competitor deal win (graph)",
    ts: T(35),
  },
  {
    signal_id: 1044,
    version: 1,
    score: 71,
    status: "NEW",
    reason: "USFDA warning letter after the close",
    ts: D(0.6),
  },
];

const news_items: Row[] = [
  [
    9001,
    "nse_announcements",
    "BHARTIARTL: Revision in tariffs of prepaid plans",
    T(98),
    "Tariffs revised by 12-15% across prepaid plans effective 3-Nov-2026.",
  ],
  [
    9002,
    "et_markets",
    "Airtel hikes prepaid tariffs up to 15%, follows Jio",
    T(90),
    null,
  ],
  [
    9003,
    "deep:bulk_deal:rule",
    "BHARTIARTL: bulk deal: mf dii buy (SBI MUTUAL FUND), Rs 800.25 cr",
    T(300),
    "Deal date 14-Oct at Rs 1600.5\nEvidence: hand-set rule (low weight; event study n=3 < 5 events for deal_buy_mf_dii)",
  ],
  [
    9010,
    "kg:1:competitor_of:base_rate:propagated",
    "TCS: via INFY > competitor_of > TCS | INFY: Large deal win with European bank",
    T(40),
    "INFY competitor_of TCS (weight 0.60): event-study base rate for kg:competitor_of:order_win: same (n=9, avg +0.9%, hit 67%)",
  ],
  [
    9011,
    "nse_announcements",
    "INFY: Award of large deal by a European bank",
    T(42),
    "Multi-year deal, TCV about USD 1.5 bn.",
  ],
  [
    9020,
    "deep:usfda:rule",
    "SUNPHARMA: Sun Pharmaceutical Industries Limited - 696123 - Warning Letter",
    D(0.6),
    "USFDA warning_letter (name match 1.00 via 'Sun Pharmaceutical Industries')\nEvidence: hand-set rule (low weight; event study n=4 < 5 events for usfda_adverse)",
  ],
  [
    9030,
    "moneycontrol",
    "Steel prices firm up as China cuts output",
    T(150),
    null,
  ],
  [
    9031,
    "deep:results:rule",
    "MARUTI: Q results Sep-2026: beat vs own trend (surprise +0.42)",
    D(1.1),
    "Consolidated (xbrl). Revenue +14.0% YoY (trend +10.0%), profit +22.0% YoY",
  ],
  [
    9032,
    "business_standard",
    "RBI keeps repo rate unchanged at 5.50%",
    D(1.5),
    null,
  ],
].map(([id, source, title, published_at, body]) => ({
  id,
  source,
  title,
  body,
  url: `https://example.com/n/${id}`,
  published_at,
  fetched_at: published_at,
  status: "tagged",
  cluster_id: null,
}));

const news_tags: Row[] = [
  [9001, "BHARTIARTL", "Telecom", "policy", "bullish", 0.8, 0.9],
  [9002, "BHARTIARTL", "Telecom", "policy", "bullish", 0.7, 0.6],
  [9003, "BHARTIARTL", "Telecom", "deal_buy_mf_dii", "bullish", 0.4, 0.6],
  [9010, "TCS", "IT", "kg:competitor_of:order_win", "bullish", 0.41, 0.6],
  [9011, "INFY", "IT", "order_win", "bullish", 0.75, 0.8],
  [9020, "SUNPHARMA", "Pharma", "usfda_adverse", "bearish", -0.7, 0.6],
  [9030, null, "Metals", "commodity", "bullish", 0.5, 0.7],
  [9031, "MARUTI", "Auto", "results_beat", "bullish", 0.7, 0.6],
  [9032, null, "Banks", "macro_data", "bearish", -0.2, 0.4],
].map(
  (
    [news_id, symbol, sector, event_type, direction, sentiment, novelty],
    i,
  ) => ({
    id: 800 + i,
    news_id,
    symbol,
    sector,
    event_type,
    direction,
    sentiment,
    novelty,
    theme: null,
  }),
);

const kg_paths: Row[] = [
  {
    id: 1,
    source_news_id: 9011,
    source_symbol: "INFY",
    target: "TCS",
    path_key: "INFY>competitor_of>TCS",
    hops: 1,
    role: "propagated",
    sign_basis: "base_rate",
    weight: 0.6,
    sentiment: 0.41,
    news_id: 9010,
    created_at: T(40),
    detail_json: null,
  },
  {
    id: 2,
    source_news_id: 9011,
    source_symbol: "INFY",
    target: "WIPRO",
    path_key: "INFY>competitor_of>WIPRO",
    hops: 1,
    role: "support",
    sign_basis: "base_rate",
    weight: 0.18,
    sentiment: 0.3,
    news_id: null,
    created_at: T(40),
    detail_json: null,
  },
  {
    id: 3,
    source_news_id: 9001,
    source_symbol: "BHARTIARTL",
    target: "TCS",
    path_key: "BHARTIARTL>customer_of>TCS",
    hops: 1,
    role: "unknown",
    sign_basis: "unknown",
    weight: 0.21,
    sentiment: null,
    news_id: null,
    created_at: T(95),
    detail_json: null,
  },
];

const kg_nodes: Row[] = [
  "INFY",
  "TCS",
  "WIPRO",
  "MARUTI",
  "MOTHERSON",
  "TATASTEEL",
  "BHARTIARTL",
].map((s, i) => ({ id: i + 1, kind: "company", name: s, symbol: s }));
const kgId = (s: string) => (kg_nodes.find((n) => n.symbol === s) as Row).id;
const kg_edges: Row[] = [
  ["INFY", "TCS", "competitor_of", 0.18, null, null, "sub_sector"],
  ["TCS", "INFY", "competitor_of", 0.18, null, null, "sub_sector"],
  ["INFY", "WIPRO", "competitor_of", 0.18, null, null, "sub_sector"],
  ["MOTHERSON", "MARUTI", "supplier_of", 0.7, "revenue_share", 25, "ai"],
  ["MARUTI", "MOTHERSON", "customer_of", 0.21, null, null, "ai"],
  ["BHARTIARTL", "TCS", "customer_of", 0.21, null, null, "seed"],
].map(([a, b, type, weight, exposure_kind, exposure_pct, origin], i) => ({
  id: i + 1,
  src: kgId(a as string),
  dst: kgId(b as string),
  type,
  weight,
  exposure_kind,
  exposure_pct,
  origin,
  valid_from: null,
  confidence: 0.9,
  updated_at: D(1),
}));

const premiumPath = (entry: number, drift: number, n: number) =>
  Array.from(
    { length: n },
    (_, i) =>
      +(entry * (1 + (drift * i) / n + Math.sin(i / 2) * 0.03)).toFixed(2),
  );

const watch = (
  id: number,
  sym: string,
  k: number,
  opt: string,
  status: string,
  pnl: number | null,
  gate: string,
): Row => ({
  id,
  signal_id: id + 3000,
  book: "watchlist",
  symbol: sym,
  option_type: opt,
  strike: k,
  expiry: "2026-11-24",
  lots: 3,
  lot_size: 400,
  entry_ts: D(status === "OPEN" ? 1 : 6),
  entry_premium: 20,
  sl: 10,
  t1: 25,
  t2: 30,
  t3: 40,
  status,
  last_premium: status === "OPEN" ? 22 : null,
  last_ts: T(5),
  exit_ts: status === "OPEN" ? null : D(2),
  exit_premium: pnl == null ? null : 20 * (1 + pnl / 100),
  exit_reason: status === "OPEN" ? null : pnl! > 0 ? "T2" : "SL",
  pnl: pnl == null ? null : pnl * 240,
  pnl_pct: pnl,
  fillability: "fillable",
  booking_mode: "per_lot",
  state_json: { targets_hit: pnl! > 0 ? 2 : 0 },
  watch_gate: gate,
});

const paper_trades: Row[] = [
  watch(901, "INFY", 1500, "CE", "CLOSED", 18.5, "edge_ratio"),
  watch(902, "SUNPHARMA", 1700, "PE", "CLOSED", -31.0, "p_t1_before_sl"),
  watch(903, "WIPRO", 300, "CE", "OPEN", null, "ev_pct"),
  {
    id: 210,
    signal_id: 1039,
    symbol: "TATASTEEL",
    option_type: "CE",
    strike: 162,
    expiry: "2026-10-29",
    lots: 3,
    lot_size: 5500,
    entry_ts: D(2),
    entry_premium: 4.2,
    sl: 2.1,
    t1: 5.25,
    t2: 6.3,
    t3: 8.4,
    status: "OPEN",
    last_premium: 5.6,
    last_ts: T(4),
    entry_fill: "fillable",
    fillability: "fillable",
    booking_mode: "per_lot",
    slippage_inr: -275,
    pnl: null,
    pnl_pct: null,
    state_json: {
      targets_hit: 1,
      stop: 4.2,
      fills: [{ event: "T1", fraction: 1 / 3, price: 5.3 }],
    },
    theme_key: "steel",
  },
  {
    id: 211,
    signal_id: 1035,
    symbol: "MARUTI",
    option_type: "CE",
    strike: 12600,
    expiry: "2026-10-29",
    lots: 3,
    lot_size: 50,
    entry_ts: D(5),
    entry_premium: 210,
    sl: 105,
    t1: 262.5,
    t2: 315,
    t3: 420,
    status: "OPEN",
    last_premium: 198,
    last_ts: T(4),
    entry_fill: "partial",
    fillability: "partial",
    booking_mode: "per_lot",
    slippage_inr: 0,
    state_json: { targets_hit: 0, stop: 105, fills: [] },
    theme_key: "auto",
  },
  {
    id: 205,
    signal_id: 1030,
    symbol: "HDFCBANK",
    option_type: "PE",
    strike: 1680,
    expiry: "2026-10-29",
    lots: 3,
    lot_size: 550,
    entry_ts: D(9),
    entry_premium: 22,
    sl: 11,
    t1: 27.5,
    t2: 33,
    t3: 44,
    status: "CLOSED",
    exit_ts: D(6),
    exit_premium: 29.4,
    exit_reason: "TRAIL",
    pnl: 11480,
    pnl_pct: 31.6,
    fillability: "fillable",
    booking_mode: "per_lot",
    slippage_inr: -820,
    attribution_json: {
      delta: 13900,
      theta: -2100,
      vega: 600,
      spread: -1100,
      residual: 180,
    },
  },
  {
    id: 203,
    signal_id: 1028,
    symbol: "WIPRO",
    option_type: "PE",
    strike: 252,
    expiry: "2026-10-29",
    lots: 3,
    lot_size: 3000,
    entry_ts: D(12),
    entry_premium: 6.1,
    sl: 3.05,
    t1: 7.6,
    t2: 9.15,
    t3: 12.2,
    status: "CLOSED",
    exit_ts: D(8),
    exit_premium: 3.0,
    exit_reason: "SL",
    pnl: -28400,
    pnl_pct: -51.7,
    fillability: "unfillable",
    booking_mode: "per_lot",
    slippage_inr: -450,
    attribution_json: {
      delta: -21000,
      theta: -4300,
      vega: -1800,
      spread: -1600,
      residual: 300,
    },
  },
];

const trade_events: Row[] = [
  {
    id: 1,
    trade_id: 210,
    ts: D(2),
    event: "ENTRY",
    premium: 4.2,
    lots: 3,
    trigger_level: null,
    slippage_inr: null,
    gap: false,
    note: null,
    spot: 158.4,
  },
  {
    id: 2,
    trade_id: 210,
    ts: D(1),
    event: "T1",
    premium: 5.3,
    lots: 1,
    trigger_level: 5.25,
    slippage_inr: 275,
    gap: false,
    note: null,
    spot: 161.2,
  },
  {
    id: 3,
    trade_id: 205,
    ts: D(9),
    event: "ENTRY",
    premium: 22,
    lots: 3,
    gap: false,
  },
  {
    id: 4,
    trade_id: 205,
    ts: D(8),
    event: "T1",
    premium: 27.8,
    lots: 1,
    trigger_level: 27.5,
    slippage_inr: 165,
    gap: false,
  },
  {
    id: 5,
    trade_id: 205,
    ts: D(7),
    event: "T2",
    premium: 33.4,
    lots: 1,
    trigger_level: 33,
    slippage_inr: 220,
    gap: false,
  },
  {
    id: 6,
    trade_id: 205,
    ts: D(6),
    event: "TRAIL",
    premium: 29.4,
    lots: 1,
    trigger_level: 27.5,
    slippage_inr: 1045,
    gap: false,
    note: "trail at T1",
  },
];

const portfolio_snapshots: Row[] = Array.from({ length: 30 }, (_, i) => {
  const eq = 1_000_000 + Math.round(Math.sin(i / 4) * 18000 + i * 1600);
  return {
    ts: D(29 - i),
    capital: 1_000_000,
    realised_inr: eq - 1_000_000 - 4000,
    unrealised_inr: 4000,
    equity: eq,
    peak_equity: 1_050_000,
    drawdown_pct: Math.max(0, ((1_050_000 - eq) / 1_050_000) * 100),
    open_trades: 2,
    premium_at_risk_inr: 98000,
    capital_used_inr: 101000,
    exposure_json: { steel: 69300, auto: 31500 },
  };
});

const rejected_candidates: Row[] = [
  {
    id: 1,
    ts: T(60),
    symbol: "INFY",
    stage: "options_edge",
    reason: "edge ratio 0.92 below 1.2 (move largely priced in)",
    direction: "bullish",
    score: 64,
    outcome_status: "open",
    option_return_pct: 6.2,
    details_json: {},
  },
  {
    id: 2,
    ts: T(140),
    symbol: "SBIN",
    stage: "ai_cap",
    reason: "daily AI caps reached (panel 4/4, single 5/5)",
    direction: "bearish",
    score: 58,
    outcome_status: "open",
    option_return_pct: null,
    details_json: {},
  },
  {
    id: 3,
    ts: T(200),
    symbol: "WIPRO",
    stage: "kg_cap",
    reason: "graph propagation cap (10/day) reached",
    direction: "bullish",
    score: null,
    outcome_status: "open",
    option_return_pct: -3.4,
    details_json: { path: "INFY>competitor_of>WIPRO" },
  },
  {
    id: 4,
    ts: D(3),
    symbol: "MOTHERSON",
    stage: "entry",
    reason:
      "by entry time the stock moved +2.1% = 72% of the predicted 2.9% (limit 60%)",
    direction: "bullish",
    score: 61,
    outcome_status: "resolved",
    option_return_pct: 14.5,
    stock_return_pct: 3.2,
    resolved_at: D(1),
    details_json: {},
  },
  {
    id: 5,
    ts: D(4),
    symbol: "TCS",
    stage: "candidate",
    reason: "single unofficial source without strong evidence",
    direction: "bearish",
    score: null,
    outcome_status: "resolved",
    option_return_pct: -22,
    resolved_at: D(2),
    details_json: { strength: 0.31 },
  },
];

const events_calendar: Row[] = [
  {
    id: 1,
    symbol: "TCS",
    event_type: "results",
    event_date: dateOnly(0),
    source: "nse",
    details: { purpose: "Financial Results" },
  },
  {
    id: 2,
    symbol: "HDFCBANK",
    event_type: "results",
    event_date: dateOnly(2),
    source: "nse",
    details: {},
  },
  {
    id: 3,
    symbol: null,
    event_type: "holiday",
    event_date: dateOnly(6),
    source: "nse",
    details: { name: "Diwali" },
  },
  {
    id: 4,
    symbol: null,
    event_type: "expiry",
    event_date: "2026-10-29",
    source: "derived",
    details: {},
  },
  {
    id: 5,
    symbol: null,
    event_type: "macro",
    event_date: dateOnly(1),
    source: "manual",
    details: { name: "CPI (Sep)" },
  },
];

const macro_snapshots: Row[] = Array.from({ length: 24 }, (_, i) => ({
  ts: T((23 - i) * 60 + 25),
  brent: +(81.2 + Math.sin(i / 3) * 0.8 + i * 0.05).toFixed(2),
  usdinr: +(84.1 + i * 0.01).toFixed(2),
  dxy: 103.4,
  us10y: 4.12,
  india_vix: +(13.2 + Math.cos(i / 4) * 0.6).toFixed(2),
  fii_net: i === 3 ? -1234.5 : null,
  dii_net: i === 3 ? 987 : null,
  adr_json: {
    INFY: { adr: "INFY", last: 19.1, change_pct: 1.4 },
    WIPRO: { adr: "WIT", last: 3.1, change_pct: -0.4 },
  },
  commodities_json: {
    gold: 2412,
    silver: 30.9,
    copper: 4.42,
    aluminium: 2580,
    natgas: 2.71,
    steel_hrc: 845 + i,
  },
}));

const macro_releases: Row[] = [
  {
    id: 1,
    name: "cpi",
    period: "2026-09-01",
    value: 4.1,
    prior: 4.4,
    released_at: D(3),
    source: "pib_finance",
  },
  {
    id: 2,
    name: "iip",
    period: "2026-08-01",
    value: 3.6,
    prior: 4.9,
    released_at: D(4),
    source: "pib_finance",
  },
  {
    id: 3,
    name: "repo_rate",
    period: "2026-10-01",
    value: 5.5,
    prior: 5.5,
    released_at: D(6),
    source: "rbi_press",
  },
];

const prediction_scores: Row[] = Array.from({ length: 40 }, (_, i) => {
  const p = 0.45 + (i % 10) * 0.035;
  return {
    id: i + 1,
    analysis_id: 400 + i,
    symbol: "X",
    direction: "bullish",
    predicted_move_pct: 2,
    p_move: p,
    window_days: 3,
    spot_start: 100,
    created_at: D(40 - i),
    status: "scored",
    direction_hit: (i * 7) % 10 < p * 10,
    prediction_source: i % 5 === 4 ? "agent" : i % 3 ? "single" : "panel",
    ai_preset: i % 5 === 4 ? "budget" : null,
  };
});

const backtest_runs: Row[] = [
  ["rules: v2.1 rules, per_lot", 412, 6.8, 0.52, -18.2, [2.1, 11.4]],
  ["rules: fixed rules, per_lot", 412, 4.1, 0.49, -22.5, [-0.9, 9.0]],
  ["rules: + knowledge graph (2 hops)", 468, 7.2, 0.53, -19.0, [2.4, 12.1]],
  ["rules: + knowledge graph (3 hops)", 503, 5.9, 0.51, -21.3, [0.6, 11.2]],
  ["benchmark: random direction", 412, -9.5, 0.41, -48.0, [-14.0, -5.1]],
].map(([name, n, exp, win, dd, ci], i) => ({
  id: 60 + i,
  config_hash: `h${i}`,
  config_json: {},
  period_start: "2021-10-01",
  period_end: "2026-09-30",
  status: "done",
  started_at: D(2),
  finished_at: D(2),
  metrics_json: {
    variant: { name },
    all: { n, expectancy_pct: exp, win_rate: win, ci90: ci },
    risk: { max_drawdown_pct: dd },
    equity: Array.from(
      { length: 20 },
      (_, k) => 100 + (exp as number) * k * 0.8 + Math.sin(k + i) * 4,
    ),
    r1_note:
      "AI replay uses only predictions after AI_MODEL_KNOWLEDGE_CUTOFF (R1).",
  },
  notes: i === 2 ? "VERDICT: not significant (CIs overlap)" : null,
}));

const event_library: Row[] = [
  ["results_beat", "all", 0, 186, 1.9, 0.64],
  ["results_beat", "all", 3, 186, 2.6, 0.66],
  ["results_miss", "all", 3, 171, -2.9, 0.31],
  ["deal_buy_promoter", "all", 3, 48, 1.4, 0.6],
  ["deal_buy_fii", "all", 3, 3, 0.8, 0.67],
  ["usfda_adverse", "all", 3, 4, -3.8, 0.25],
  ["kg:competitor_of:order_win", "all", 3, 9, 0.9, 0.67],
  ["kg:competitor_of:disaster", "all", 3, 6, 1.6, 0.67],
  ["order_win", "sector:IT", 3, 32, 1.2, 0.59],
].map(([event_type, level_key, window, n, mean, hit], i) => ({
  id: i + 1,
  event_type,
  level_key,
  regime: "all",
  window,
  n,
  n_eff: (n as number) * 0.9,
  mean,
  median: (mean as number) * 0.9,
  hit_rate: hit,
  p25: (mean as number) - 2,
  p75: (mean as number) + 2,
  computed_at: D(4),
  examples_json:
    i === 1
      ? [
          {
            date: "2026-07-17",
            symbol: "TCS",
            direction: "bullish",
            abnormal_pct: 3.1,
            title: "TCS Q1 beat",
          },
        ]
      : null,
}));

const digests: Row[] = [
  {
    id: 3,
    week_start: "2026-10-05",
    created_at: D(4),
    data_json: {},
    text: "WEEKLY DIGEST 05-Oct to 11-Oct\n\nTRADES\n  All: 4 trades, win rate 50%, P&L Rs +8,120\n  Excluding unfillable: 3 trades, win rate 67%\n\nREGULATOR ITEMS TO REVIEW (name match below threshold: no evidence created)\n  USFDA: Sun Pharma Advanced Research - WL | best 0.80: SUNPHARMA 0.80\n\nSUGGESTIONS (not applied; change config yourself if you agree)\n  - edge-ratio rejections would have won 3 of 4: review EDGE_RATIO_MIN",
  },
];

const positioning_snapshots: Row[] = [
  {
    day: "2026-10-14",
    symbol: "BHARTIARTL",
    source: "eod",
    computed_at: D(1),
    states_json: {
      oi_buildup: ["long_buildup", 1.8],
      pcr: ["high_rising", 1.42],
      iv_skew: ["flat", 1.1],
      term_structure: ["inverted", 4.2],
      basis: ["flat", 0.4],
      unusual_activity: ["unusual_calls", 5.3],
      delivery: ["high", 18.5],
    },
  },
  {
    day: "2026-10-13",
    symbol: "BHARTIARTL",
    source: "eod",
    computed_at: D(2),
    states_json: {
      oi_buildup: ["flat", 0.2],
      pcr: ["neutral", 1.05],
      delivery: ["normal", 2.0],
    },
  },
];

const market_regime: Row[] = [
  {
    day: "2026-10-15",
    vol: "normal",
    direction: "risk_on",
    label: "normal/risk_on",
    inputs: "VIX+Nifty+FII",
    vix: 14.2,
    vix_pct: 46,
    nifty_ret_pct: 2.3,
    fii_net_cr: 4120,
    source: "live",
    computed_at: D(0),
  },
  {
    day: "2026-10-14",
    vol: "normal",
    direction: "risk_on",
    label: "normal/risk_on",
    inputs: "VIX+Nifty; FII unknown",
    vix: 14.6,
    vix_pct: 49,
    nifty_ret_pct: 1.9,
    fii_net_cr: null,
    source: "history",
    computed_at: D(1),
  },
];

const golive_checks: Row[] = [
  {
    id: 3,
    kind: "auto",
    created_at: D(0),
    status: "ok",
    text: "POSITIONING HISTORY READY (automatic)",
    results_json: {
      title: "Positioning history (automatic)",
      checks: [
        {
          name: "positioning_history",
          status: "ok",
          summary: "1236 days, 948210 states, 61 base rates",
          details: [
            "futures: 1236 days parsed from the cache, 0 errors",
            "nightly job adds each new day from now on",
          ],
        },
      ],
    },
  },
  {
    id: 1,
    kind: "quick",
    created_at: D(1),
    status: "fail",
    text: "GO-LIVE QUICK CHECK (2 h)",
    results_json: {
      checks: [
        {
          name: "nse_probe",
          status: "fail",
          summary:
            "2 of 14 NSE endpoints BLOCKED/THROTTLED from this server (direct (no NSE_HTTP_PROXY)). Set NSE_HTTP_PROXY or use the listed alternatives.",
          details: [
            "announcements: OK",
            "insider: BLOCKED (HTTP 403) -> alternative: BSE insider disclosures (bseindia.com)",
            "fo_bhavcopy: THROTTLED (slow (9100 ms)) -> alternative: Upstox option chain (live only)",
          ],
        },
        {
          name: "database",
          status: "ok",
          summary: "migration 0014 (head 0014)",
          details: [],
        },
        {
          name: "upstox",
          status: "ok",
          summary: "token expires in 351 days",
          details: [
            "token expires in 351 days",
            "4120 option quotes stored since start",
          ],
        },
        {
          name: "ai_smoke",
          status: "ok",
          summary: "passed: Rs 31, panel 96s",
          details: [],
        },
      ],
    },
  },
  {
    id: 2,
    kind: "full",
    created_at: D(0),
    status: "warn",
    text: "FIRST-RUN REPORT (24 h)",
    results_json: {
      checks: [
        {
          name: "nse_probe",
          status: "ok",
          summary: "all 14 NSE endpoints reachable (via NSE_HTTP_PROXY)",
          details: [],
        },
        {
          name: "unverified",
          status: "warn",
          summary:
            "docs/UNVERIFIED.md: 20 verified automatically, 16 need a manual step",
          details: [
            "#1: verified (nse_probe)",
            "#21: needs you - After a week: share of bulk/block deals classed 'other'",
          ],
        },
      ],
    },
  },
];

const JOBS: [string, number, string, string | null][] = [
  ["signal_cycle", 3, "ok", null],
  ["trading_cycle", 4, "ok", null],
  ["portfolio_snapshot", 12, "ok", null],
  ["tag_news", 2, "ok", null],
  ["news_day", 6, "ok", null],
  ["macro", 52, "ok", null],
  ["heartbeat", 1, "ok", null],
  ["event_calendar", 236, "ok", null],
  ["kg_build", 260, "ok", null],
  ["calibration_resolve", 1170, "ok", null],
  ["backtest_auto", 780, "ok", null],
  ["history_weekly", 5900, "ok", null],
  ["weekly_digest", 5900, "ok", null],
  ["deep_insider", 9, "error", "HTTPStatusError: 403 Forbidden"],
  ["results_slow_path", 10, "ok", null],
  ["monthly_report", 20000, "ok", null],
  ["learning_suggest", 20030, "ok", null],
  ["ai_vs_rules", 1140, "ok", null],
  ["data_quality", 970, "ok", null],
];
const job_runs: Row[] = JOBS.map(([job, ago, status, error], i) => ({
  id: i + 1,
  job,
  started_at: T(ago + 0.5),
  finished_at: T(ago),
  status,
  error,
  items_processed: status === "ok" ? 3 : null,
}));
job_runs.push({
  id: 99,
  job: "deep_insider",
  started_at: T(130),
  finished_at: T(130),
  status: "ok",
  error: null,
  items_processed: 12,
});

const config: Row[] = [
  { key: "feat.event_study", value_json: true },
  { key: "feat.deep_data", value_json: true },
  { key: "feat.knowledge_graph", value_json: true },
  { key: "brief.morning_enabled", value_json: true },
  { key: "brief.evening_enabled", value_json: true },
  { key: "alerts_muted", value_json: false },
  {
    key: "source_health",
    value_json: {
      nse_announcements: { last_ok: T(8), consecutive_failures: 0 },
      nse_insider: {
        last_ok: T(130),
        last_error: "HTTPStatusError: 403 Forbidden",
        last_error_at: T(9),
        consecutive_failures: 4,
      },
      macro_yahoo: { last_ok: T(52), consecutive_failures: 0 },
      upstox: { last_ok: T(4), consecutive_failures: 0 },
      sebi_rss: { last_ok: T(25), consecutive_failures: 0 },
    },
  },
  {
    key: "data_quality",
    value_json: {
      checked_at: T(970),
      counts: { bid_above_ask: 3, stale_chain: 1, spot_jump: 1 },
      issues: {
        bid_above_ask: [
          "TCS 4150CE 14-Oct 14:05: bid 31.2 > ask 30.9",
          "TCS 4150CE 14-Oct 14:07: bid 31.4 > ask 31.0",
          "INFY 1500PE 14-Oct 11:20: bid 8.1 > ask 8.0",
        ],
        stale_chain: ["MARUTI: last quote 11:05"],
        spot_jump: [
          "HDFCBANK: 1650 -> 1990 at 14-Oct 10:15 without a corporate action",
        ],
      },
    },
  },
  {
    key: "ai_cost_report",
    value_json: {
      month: "2026-10-01",
      models: {
        "tagger-model": { cost_inr: 96.2, calls: 1840, cache_hit_rate: 0.62 },
        "judge-model": { cost_inr: 512.4, calls: 41, cache_hit_rate: 0.18 },
        "analyst-model": { cost_inr: 188, calls: 52, cache_hit_rate: 0.21 },
      },
    },
  },
  {
    key: "db_size",
    value_json: {
      total_mb: 212.4,
      limit_mb: 500,
      largest: { option_quotes: 118.2, news_items: 31.5, job_runs: 12.1 },
    },
  },
  {
    key: "unverified_alerts",
    value_json: [
      {
        item: "Upstox depth quantity field missing: using chain quantities",
        since: D(1),
      },
    ],
  },
];

const config_audit: Row[] = [
  {
    id: 1,
    ts: D(1),
    key: "feat.knowledge_graph",
    old_value: false,
    new_value: true,
    changed_by: "owner@example.com",
  },
  {
    id: 2,
    ts: T(300),
    key: "alerts_muted",
    old_value: false,
    new_value: true,
    changed_by: "postgres",
  },
  {
    id: 3,
    ts: T(240),
    key: "alerts_muted",
    old_value: true,
    new_value: false,
    changed_by: "owner@example.com",
  },
];

const ai_usage: Row[] = [
  {
    id: 1,
    ts: T(96),
    model: "judge-model",
    input_tokens: 9000,
    output_tokens: 1800,
    cached_tokens: 0,
    cost_inr: 8.7,
  },
  {
    id: 2,
    ts: T(96),
    model: "bull-model",
    input_tokens: 6000,
    output_tokens: 1400,
    cached_tokens: 0,
    cost_inr: 6.0,
  },
  {
    id: 3,
    ts: T(36),
    model: "analyst-model",
    input_tokens: 7000,
    output_tokens: 1500,
    cached_tokens: 0,
    cost_inr: 3.6,
  },
  {
    id: 4,
    ts: T(20),
    model: "tagger-model",
    input_tokens: 40000,
    output_tokens: 8000,
    cached_tokens: 20000,
    cost_inr: 1.4,
  },
];

const ai_smoke_runs: Row[] = [
  {
    id: 1,
    started_at: D(6),
    finished_at: D(6),
    trigger: "startup",
    passed: true,
    total_cost_inr: 31.2,
    panel_seconds: 96,
    results_json: {},
  },
];

const regulatory_actions: Row[] = [
  {
    id: 1,
    ref: "r1",
    regulator: "usfda",
    action_type: "warning_letter",
    symbol: null,
    title: "Sun Pharma Advanced Research Company - Warning Letter",
    url: "https://fda/x",
    disclosed_at: D(2),
    match_confidence: 0.8,
    match_candidates: [{ symbol: "SUNPHARMA", score: 0.8, via: "Sun Pharma" }],
  },
  {
    id: 2,
    ref: "r2",
    regulator: "usfda",
    action_type: "warning_letter",
    symbol: "SUNPHARMA",
    title: "Sun Pharmaceutical Industries Limited - 696123",
    url: "https://fda/y",
    disclosed_at: D(0.6),
    match_confidence: 1,
  },
];

const calibrators: Row[] = [];

const monthly_reports: Row[] = [
  {
    id: 1,
    month: "2026-09-01",
    created_at: D(14),
    ai_summary_status: "ok",
    ai_summary:
      "September closed 18 paper trades with a 56% win rate and an expectancy of +6.4% of premium; excluding unfillable trades it was +8.1%. Results beats were the best trigger type (+21.3%, n=5); bulk deals by MF/DII were the weakest (-14.2%, n=3). AI spend was Rs 1,240 of the Rs 2,000 budget.",
    text: "MONTHLY REPORT 2026-09 (paper trading)\n\nP&L\n  All: 18 trades, win rate 56%, expectancy +6.4% of premium (90% interval -2.1..+14.8%), P&L Rs +41,200\n  Excluding unfillable: 16 trades, win rate 62%, expectancy +8.1% of premium, P&L Rs +47,900\n\nWHAT WORKED\n  Trigger types: results_beat +21.3% (n=5), order_win +9.8% (n=4)\n  Sectors: IT +12.0% (n=6)\nWHAT DID NOT\n  Trigger types: deal_buy_mf_dii -14.2% (n=3)\n  Sectors: Banks -6.5% (n=4)\n\nAI VS RULES\n  ai: 22 calls, hit 59%, avg move +0.8%\n  rules: 15 calls, hit 53%, avg move +0.4%\n\nPOST-MORTEM CAUSES\n  priced_in: 3\n  thesis_wrong: 2\n\nAI spend Rs 1,240 of Rs 2,000 budget\nJobs below 95% success: deep_insider 81%",
    data_json: {
      month: "2026-09",
      trades: {
        all: {
          n: 18,
          win_rate: 0.556,
          expectancy_pct: 6.4,
          ci: [-2.1, 14.8],
          pnl_inr: 41200,
        },
        excluding_unfillable: {
          n: 16,
          win_rate: 0.625,
          expectancy_pct: 8.1,
          pnl_inr: 47900,
        },
      },
      worked: {
        trigger_types: [
          { name: "results_beat", n: 5, expectancy_pct: 21.3 },
          { name: "order_win", n: 4, expectancy_pct: 9.8 },
        ],
        sectors: [{ name: "IT", n: 6, expectancy_pct: 12 }],
      },
      did_not_work: {
        trigger_types: [
          { name: "deal_buy_mf_dii", n: 3, expectancy_pct: -14.2 },
        ],
        sectors: [{ name: "Banks", n: 4, expectancy_pct: -6.5 }],
      },
      ai_vs_rules: {
        resolved: 37,
        ai: { n: 22, hit_rate: 0.59, avg_move_pct: 0.8 },
        rules: { n: 15, hit_rate: 0.53, avg_move_pct: 0.4 },
      },
      ai_spend_inr: 1240,
      ai_budget_inr: 2000,
      risk: { max_drawdown_pct: 3.2, sharpe: 1.4 },
    },
  },
];

const suggestions: Row[] = [
  {
    id: 11,
    month: "2026-10-01",
    kind: "change",
    param: "edge_ratio_min",
    file: "worker/config/options.yaml",
    yaml_path: "edge_ratio_min",
    current_value: 1.2,
    proposed_value: 1.3,
    expected_effect: 18.6,
    status: "pending",
    created_at: D(14),
    pr_url: null,
    evidence_json: {
      n: 64,
      mean_diff_pct: 0.87,
      ci: [0.21, 1.62],
      ci_level: 0.975,
      halves: [0.71, 1.02],
      stress_mean_diff_pct: 0.44,
      months: 3,
    },
  },
  {
    id: 9,
    month: "2026-09-01",
    kind: "change",
    param: "min_p_t1_before_sl",
    file: "worker/config/options.yaml",
    yaml_path: "selection.min_p_t1_before_sl",
    current_value: 0.55,
    proposed_value: 0.6,
    expected_effect: 9.2,
    status: "applied",
    applied_at: D(14),
    decided_at: D(30),
    decided_by: "owner@example.com",
    pr_url: "https://github.com/owner/repo/pull/21",
    pr_number: 21,
    created_at: D(44),
    review_json: null,
    evidence_json: {
      n: 58,
      mean_diff_pct: 0.48,
      ci: [0.05, 0.94],
      ci_level: 0.975,
      halves: [0.4, 0.55],
      stress_mean_diff_pct: 0.2,
    },
  },
  {
    id: 7,
    month: "2026-08-01",
    kind: "change",
    param: "signal_score_threshold",
    file: "worker/config/signals.yaml",
    yaml_path: "thresholds.signal_score",
    current_value: 65,
    proposed_value: 70,
    expected_effect: 6.1,
    status: "rejected",
    decided_at: D(60),
    decided_by: "owner@example.com",
    created_at: D(75),
    evidence_json: { n: 41, ci: [0.02, 0.6], ci_level: 0.975 },
  },
];

const ai_vs_rules: Row[] = Array.from({ length: 30 }, (_, i) => {
  const move = +(Math.sin(i * 1.7) * 3).toFixed(2);
  const ai = i % 5 !== 0;
  const rules = i % 3 !== 0 ? "bullish" : null;
  return {
    id: i + 1,
    analysis_id: 400 + i,
    symbol: ["TCS", "INFY", "SBIN", "MARUTI"][i % 4],
    created_at: D(30 - i),
    news_direction: "bullish",
    ai_direction: i % 4 === 0 ? "bearish" : "bullish",
    ai_signal: ai,
    rules_direction: rules,
    rules_reason: rules ? "supported by analogues" : "no usable analogues",
    analogue_n: rules ? 24 : 2,
    analogue_mean: rules ? 1.4 : null,
    realised_move_pct: i < 26 ? move : null,
    resolved_at: i < 26 ? D(28 - i) : null,
  };
});

// ---- Phase 13 research agent ----------------------------------------------------------------
const research_runs: Row[] = [
  { id: 501, kind: "live", role: "research_live", preset: "budget", provider: "anthropic", model: "claude-sonnet-5-5", effort: "medium",
    symbol: "TCS", subject: "stock:TCS", analysis_id: 502, research_file_id: 72, status: "ok", limited_reason: null,
    tool_calls: 6, web_searches: 2, web_fetches: 1, input_tokens: 61_200, output_tokens: 7_900, cache_read_tokens: 48_000, cost_inr: 23.4,
    estimate_inr: 26, duration_s: 74, created_at: T(95),
    bear_case: "The deal's value is spread over 8 years and BFSI clients are still cutting discretionary spend; a beat on the deal may not lift the quarter.",
    sources_json: [{ url: "https://www.nseindia.com/companies-listing/corporate-filings-announcements", title: "NSE announcement", kind: "fetch" }],
    lessons_json: [{ id: 4, text: "IT results beats moved less than the base rate when the rupee strengthened in the same week.", applied: true }],
    output_json: { base_rate: "order_win (IT, 3d): avg +1.8%, hit 62%, n=27", adjustments: ["Official filing with deal size: +0.4%", "Rupee firmer this week: -0.5%"],
      cited_urls: ["https://www.nseindia.com/companies-listing/corporate-filings-announcements"] } },
  { id: 502, kind: "live", role: "research_live", preset: "budget", provider: "anthropic", model: "claude-sonnet-5-5", effort: "medium",
    symbol: "INFY", subject: "stock:INFY", analysis_id: null, research_file_id: null, status: "limited", limited_reason: "tool_calls",
    tool_calls: 16, web_searches: 4, web_fetches: 2, input_tokens: 152_000, output_tokens: 14_100, cache_read_tokens: 120_000, cost_inr: 31.8,
    estimate_inr: 26, duration_s: 182, created_at: T(240), bear_case: "Guidance cut risk outweighs the deal.", sources_json: [], lessons_json: [], output_json: {} },
  { id: 503, kind: "live", role: "research_live", preset: "budget", provider: "anthropic", model: "claude-sonnet-5-5", effort: "medium",
    symbol: "SBIN", subject: "stock:SBIN", analysis_id: null, research_file_id: null, status: "failed", limited_reason: null,
    tool_calls: 3, web_searches: 0, web_fetches: 0, input_tokens: 18_000, output_tokens: 2_100, cache_read_tokens: 9_000, cost_inr: 6.2,
    estimate_inr: 26, duration_s: 31, created_at: D(1), error: "invalid answer after retry: analysis cites unknown news ids [77]", sources_json: [], lessons_json: [], output_json: {} },
  { id: 504, kind: "files", role: "research_files", preset: "budget", provider: "anthropic", model: "claude-haiku-5-5", effort: "low",
    symbol: "BHARTIARTL", subject: "stock:BHARTIARTL", status: "ok", tool_calls: 0, web_searches: 0, web_fetches: 0, input_tokens: 21_000,
    output_tokens: 2_600, cache_read_tokens: 0, cost_inr: 0.16, estimate_inr: 0.5, duration_s: null, created_at: D(1), batch_id: "msgbatch_01", research_file_id: 71 },
  { id: 505, kind: "files", role: "research_files", preset: "budget", provider: "anthropic", model: "claude-haiku-5-5", effort: "low",
    symbol: "TCS", subject: "stock:TCS", status: "ok", tool_calls: 0, web_searches: 0, web_fetches: 0, input_tokens: 24_000,
    output_tokens: 2_900, cache_read_tokens: 0, cost_inr: 0.19, estimate_inr: 0.5, duration_s: null, created_at: D(1), batch_id: "msgbatch_01", research_file_id: 72 },
  { id: 506, kind: "review", role: "research_review", preset: "budget", provider: "anthropic", model: "claude-sonnet-5-5", effort: "medium",
    subject: "week:2026-10-05", status: "ok", tool_calls: 0, web_searches: 0, web_fetches: 0, input_tokens: 34_000, output_tokens: 2_200,
    cache_read_tokens: 0, cost_inr: 8.1, estimate_inr: 25, duration_s: 40, created_at: D(4) },
];
const research_tool_calls: Row[] = [
  ["get_analogues", { symbol: "TCS", event_type: "order_win" }, 3],
  ["get_news", { symbol: "TCS", days: 7 }, 5],
  ["get_positioning", { symbol: "TCS" }, null],
  ["web_search", { query: "TCS deal win NSE filing" }, null],
  ["web_fetch", { url: "https://www.nseindia.com/companies-listing/corporate-filings-announcements" }, null],
  ["get_kg_neighbours", { symbol: "TCS" }, 4],
  ["run_simulator", { symbol: "TCS", direction: "bullish" }, null],
].map(([tool, args_json, rows], i) => ({ id: 900 + i, run_id: 501, seq: i + 1, tool, args_json, rows, is_error: false, summary: "", duration_ms: 40 + i * 9, created_at: T(96) }));
const research_files: Row[] = [
  { id: 71, subject_kind: "stock", subject: "BHARTIARTL", version: 4, provider: "anthropic", model: "claude-haiku-5-5", preset: "budget", run_id: 504, cost_inr: 0.16, created_at: D(1),
    sources_json: [],
    content_json: {
      summary: "Tariff cycle is the main driver; ARPU and capex intensity decide the re-rating.",
      thesis: "Second tariff round in 18 months; ARPU path to Rs 245 if peers follow within weeks.",
      catalysts: [{ date: dateOnly(6), event: "Q2 results", why_it_matters: "First quarter with the new tariffs", sources: ["news:3101"] }],
      key_numbers: [{ name: "ARPU (Q1)", value: "Rs 211", source: "news:3088" }],
      sensitivities: [{ driver: "Tariff hike matched by Jio", stock_move_pct: "+3 to +6", basis: "tariff_change base rate, n=19" },
        { driver: "Peers hold prices", stock_move_pct: "-1 to -3", basis: "2021 episode" }],
      sector_and_graph_links: [{ symbol: "INDUSTOWER", relation: "tower tenant" }, { symbol: "IDEA", relation: "competitor" }],
      risks: ["Regulatory pushback on tariffs", "Subscriber churn to Jio"],
      sources: [{ ref: "news:3101", note: "results date" }, { ref: "news:3088", note: "Q1 ARPU" }],
      changes_since_last: "Added the tariff filing; dropped the spectrum auction catalyst (done).",
    } },
  { id: 70, subject_kind: "stock", subject: "BHARTIARTL", version: 3, provider: "anthropic", model: "claude-haiku-5-5", preset: "budget", run_id: null, cost_inr: 0.15, created_at: D(5),
    sources_json: [], content_json: { summary: "Older version.", thesis: "", catalysts: [], key_numbers: [], sensitivities: [], sector_and_graph_links: [], risks: [], sources: [] } },
  { id: 72, subject_kind: "stock", subject: "TCS", version: 2, provider: "anthropic", model: "claude-haiku-5-5", preset: "budget", run_id: 505, cost_inr: 0.19, created_at: D(1),
    sources_json: [], content_json: { summary: "Deal wins vs discretionary spend; results in 3 sessions.", thesis: "Large deal ramp offsets BFSI softness.",
      catalysts: [{ date: dateOnly(3), event: "Q2 results", why_it_matters: "Deal TCV and margin guide", sources: ["news:3120"] }], key_numbers: [],
      sensitivities: [{ driver: "Results beat", stock_move_pct: "+2 to +4", basis: "results_beat base rate (IT), n=42" }], sector_and_graph_links: [], risks: ["Rupee strength"], sources: [] } },
  { id: 73, subject_kind: "theme", subject: "rbi_policy", version: 1, provider: "anthropic", model: "claude-haiku-5-5", preset: "budget", run_id: null, cost_inr: 0.12, created_at: D(2),
    sources_json: [], content_json: { summary: "RBI policy in 5 sessions: banks and NBFCs most sensitive.", thesis: "Hold expected.", catalysts: [], key_numbers: [],
      sensitivities: [{ driver: "Surprise cut", stock_move_pct: "+2 to +3 (PSU banks)", basis: "rbi_policy base rate" }], sector_and_graph_links: [], risks: [], sources: [] } },
];
const research_lessons: Row[] = [
  { id: 3, scope_kind: "sector", scope: "Telecom", text: "Telecom tariff hikes: the first mover's move fades when peers wait more than a week.",
    evidence_json: { prediction_ids: [11, 14] }, run_id: 506, week: dateOnly(-10), deleted: false, deleted_at: null, deleted_by: null, created_at: D(4) },
  { id: 4, scope_kind: "event_type", scope: "results_beat", text: "IT results beats moved less than the base rate when the rupee strengthened in the same week.",
    evidence_json: { prediction_ids: [12, 15, 19] }, run_id: 506, week: dateOnly(-10), deleted: false, deleted_at: null, deleted_by: null, created_at: D(4) },
  { id: 5, scope_kind: "stock", scope: "SBIN", text: "Single striking case: a PSU bank rally ignored a weak credit-cost print.",
    evidence_json: { prediction_ids: [17] }, run_id: 506, week: dateOnly(-10), deleted: false, deleted_at: null, deleted_by: null, created_at: D(4) },
];

// minimal analyses behind the scorecard / AI-vs-rules rows (prediction source + preset only)
for (let i = 0; i < 40; i++)
  analyses.push({
    id: 400 + i,
    symbol: "X",
    direction: "bullish",
    thesis: "",
    created_at: D(40 - i),
    prediction_source: i % 5 === 4 ? "agent" : i % 3 ? "single" : "panel",
    ai_preset: i % 5 === 4 ? "budget" : null,
  });

// ---- Phase 14 Strategy Lab ------------------------------------------------------------------
const labTrade = (book: string, sym: string, dir: string, entry: number, exit: number | null, pnl: number) => ({
  book, symbol: sym, direction: dir, option_type: dir === "bullish" ? "CE" : "PE",
  entry_day: dateOnly(-entry), exit_day: exit == null ? null : dateOnly(-exit), pnl_inr: pnl,
  pnl_pct: +(pnl / 300).toFixed(2), mode: "eod_incubation",
  marks: [[dateOnly(-entry), 0], [dateOnly(-(exit ?? 0)), pnl]],
});
const eodTradesA = [
  labTrade("lab:12", "TCS", "bullish", 40, 35, 2100), labTrade("lab:12", "INFY", "bullish", 33, 30, -900),
  labTrade("lab:12", "HDFCBANK", "bullish", 28, 24, 1600), labTrade("lab:12", "SBIN", "bearish", 20, 17, 700),
  labTrade("lab:12", "TCS", "bullish", 12, 8, 1250), labTrade("lab:12", "MARUTI", "bullish", 6, null, 300),
];
const eodTradesCore = [
  labTrade("news_core", "TCS", "bullish", 39, 36, 1800), labTrade("news_core", "SUNPHARMA", "bearish", 25, 22, -600),
  labTrade("news_core", "WIPRO", "bullish", 15, 11, 400),
];
const lab_hypotheses: Row[] = [
  { id: 1, name: "post_results_drift", source: "known_pattern", status: "incubating", ai_memory_risk: false, registered_at: D(60),
    rationale: "Prices under-react to clear earnings surprises and keep drifting for days (post-earnings announcement drift).",
    expected_effect: "Positive expectancy over 5 sessions after a clear beat (calls) or miss (puts)." },
  { id: 2, name: "pre_results_iv_runup", source: "known_pattern", status: "rejected", ai_memory_risk: false, registered_at: D(60),
    rationale: "Implied volatility tends to rise into scheduled results; a long option bought a week before gains vega before the announcement.",
    expected_effect: "Positive expectancy when bought 5-7 trading days before results and sold the day before." },
  { id: 3, name: "mined_order_win_it", source: "data_mining", status: "holdout_pending", ai_memory_risk: false, registered_at: D(20),
    rationale: "Pre-cutoff event study: order_win in IT: n=64, mean abnormal move +1.90% our way, hit 61% over 3 days, stable in both halves.",
    expected_effect: "Calls/puts in the news direction earn a positive expectancy after costs.", origin_json: { scanned: 212 } },
  { id: 4, name: "ai_block_deal_fii_drift", source: "ai_proposal", status: "testing", ai_memory_risk: true, registered_at: D(3),
    rationale: "FII block buys reveal informed institutional demand that keeps pushing the stock for a few sessions.",
    expected_effect: "Calls after FII block buys earn about +5% of premium per trade." },
  { id: 5, name: "news_core", source: "news_core", status: "incubating", ai_memory_risk: false, registered_at: D(60),
    rationale: "The main engine's news signals, as a strategy, so every lab strategy is compared with it on equal terms.",
    expected_effect: "Baseline: whatever the main engine earns." },
];
const specYaml = (name: string, src: string, body: string) => `name: ${name}\nsource: ${src}\n${body}`;
const lab_specs: Row[] = [
  { id: 12, hypothesis_id: 1, version: 2, name: "post_results_drift", stage: "incubating", parent_spec_id: 11, created_at: D(40),
    change_note: "grid: {'entry': {'timing': 'next_open'}}",
    spec_yaml: specYaml("post_results_drift", "known_pattern", "trigger:\n  event_types: [results_beat, results_miss]\n  direction: from_event\nentry:\n  timing: next_open\nexits:\n  max_hold_days: 5\n  rule_set: v21") },
  { id: 11, hypothesis_id: 1, version: 1, name: "post_results_drift", stage: "rejected", created_at: D(59),
    spec_yaml: specYaml("post_results_drift", "known_pattern", "trigger:\n  event_types: [results_beat, results_miss]\nexits:\n  max_hold_days: 5") },
  { id: 21, hypothesis_id: 2, version: 1, name: "pre_results_iv_runup", stage: "rejected", created_at: D(59),
    spec_yaml: specYaml("pre_results_iv_runup", "known_pattern", "entry:\n  timing: before_event\n  days_before: 6\nexits:\n  exit_before_event: true") },
  { id: 31, hypothesis_id: 3, version: 1, name: "mined_order_win_it", stage: "holdout_pending", created_at: D(19),
    spec_yaml: specYaml("mined_order_win_it", "data_mining", "trigger:\n  event_types: [order_win]\nfilters:\n  sectors: [IT]") },
  { id: 41, hypothesis_id: 4, version: 1, name: "ai_block_deal_fii_drift", stage: "stage0", created_at: D(3),
    spec_yaml: specYaml("ai_block_deal_fii_drift", "ai_proposal", "trigger:\n  event_types: [deal_buy_fii]\n  direction: bullish") },
  { id: 51, hypothesis_id: 5, version: 1, name: "news_core", stage: "incubating", created_at: D(60),
    spec_yaml: specYaml("news_core", "news_core", "trigger:\n  event_types: [results]") },
];
const lab_trials: Row[] = [
  { id: 101, spec_id: 11, kind: "stage0", label: null, n_trades: 188, n_events: 141, expectancy_pct: 1.2, ci_lo: -2.1, ci_hi: 4.4, win_rate: 0.47, created_at: D(58) },
  { id: 102, spec_id: 12, kind: "stage0", label: null, n_trades: 176, n_events: 133, expectancy_pct: 6.8, ci_lo: 2.4, ci_hi: 11.1, win_rate: 0.53, created_at: D(39) },
  { id: 103, spec_id: 12, kind: "cost", label: "spread x2", n_trades: 176, n_events: 133, expectancy_pct: 2.9, ci_lo: -1.2, ci_hi: 7.0, created_at: D(39) },
  { id: 104, spec_id: 12, kind: "plateau", label: "strike itm1", n_trades: 170, n_events: 130, expectancy_pct: 5.1, created_at: D(39) },
  { id: 105, spec_id: 12, kind: "plateau", label: "max_hold -1", n_trades: 176, n_events: 133, expectancy_pct: 4.0, created_at: D(39) },
  { id: 106, spec_id: 12, kind: "holdout", label: null, n_trades: 24, n_events: 19, expectancy_pct: 5.5, ci_lo: 0.4, ci_hi: 10.9, created_at: D(25) },
  { id: 107, spec_id: 21, kind: "stage0", label: null, n_trades: 96, n_events: 96, expectancy_pct: -4.1, ci_lo: -8.0, ci_hi: -0.3, win_rate: 0.38, created_at: D(57) },
  { id: 108, spec_id: 31, kind: "holdout_count", label: null, n_trades: 9, n_events: 7, expectancy_pct: null, created_at: D(1) },
  { id: 109, spec_id: 12, kind: "eod", label: "eod_incubation", n_trades: 5, n_events: 5, expectancy_pct: 4.1, created_at: D(1), metrics_json: { mode: "eod_incubation", trades: eodTradesA, open: 1 } },
  { id: 110, spec_id: 51, kind: "eod", label: "eod_incubation", n_trades: 3, n_events: 3, expectancy_pct: 1.8, created_at: D(1), metrics_json: { mode: "eod_incubation", trades: eodTradesCore, open: 0 } },
];
const lab_verdicts: Row[] = [
  { id: 201, spec_id: 12, stage: "stage0", passed: true, summary: "Stage 0: passed every check", created_at: D(39),
    checks_json: { checks: {
      min_trades: { passed: true, value: 176, need: 60 }, min_events: { passed: true, value: 133, need: 40 },
      ci_excludes_zero: { passed: true, value: 2.4 }, deflated_sharpe: { passed: true, value: 0.97, trials: 214, need: 0.95 },
      plateau: { passed: true, share: 0.83 }, placebo: { passed: true, real: 6.8, placebo_p: 2.2, runs: 100 },
      concentration: { passed: true, stock: 0.14, month: 0.19 }, cost_stress: { passed: true, expectancy_pct: 2.9, spread_mult: 2 },
      regime: { passed: true, share_positive: 0.67 } },
      writeup: "Calls the morning after a clear results beat (puts after a miss) earned +6.8% of premium per trade over 176 trades, with every luck test passed. It holds at double spreads (+2.9%) and does not depend on one stock or month. Next: let the holdout confirm it." } },
  { id: 202, spec_id: 12, stage: "holdout", passed: true, summary: "Stage 1 holdout: passed every check", created_at: D(25),
    checks_json: { checks: { expectancy: { passed: true, value: 5.5 }, cost_stress: { passed: true, expectancy_pct: 1.6 } } } },
  { id: 203, spec_id: 21, stage: "stage0", passed: false, summary: "Stage 0: failed ci_excludes_zero, placebo, cost_stress", created_at: D(57),
    checks_json: { checks: { ci_excludes_zero: { passed: false, value: -8.0 }, placebo: { passed: false, real: -4.1, placebo_p: 1.1, runs: 100 },
      cost_stress: { passed: false, expectancy_pct: -9.2 } } } },
];
const lab_books: Row[] = [
  { id: 1, spec_id: 12, book: "lab:12", status: "incubating", alerts_enabled: false, started_at: D(41),
    stats_json: { eod_incubation: { n: 5, events: 5, expectancy_pct: 4.1, win_rate: 0.8 } } },
  { id: 2, spec_id: 51, book: "news_core", status: "incubating", alerts_enabled: false, started_at: D(41),
    stats_json: { eod_incubation: { n: 3, events: 3, expectancy_pct: 1.8, win_rate: 0.67 },
      portfolio: { mode: "eod_incubation", trades: 7, pnl_inr: 5050, blocked: [{ book: "lab:12", symbol: "SUNPHARMA", day: dateOnly(-25), reason: "opposite of open bearish trade (news_core)" }],
        equity: Array.from({ length: 30 }, (_, i) => [dateOnly(-40 + i), 1_000_000 + Math.round(170 * i + 600 * Math.sin(i / 3))]),
        attribution: { "lab:12": { trades: 6, pnl_inr: 4750 }, news_core: { trades: 3, pnl_inr: 1600 } },
        correlation: { "lab:12": { "lab:12": 1, news_core: 0.41 }, news_core: { "lab:12": 0.41, news_core: 1 } } } } },
];

export const TABLES: Record<string, Row[]> = {
  instruments,
  signals,
  analyses,
  scenarios,
  agent_runs,
  signal_candidates,
  signal_versions,
  news_items,
  news_tags,
  kg_paths,
  kg_nodes,
  kg_edges,
  paper_trades,
  trade_events,
  portfolio_snapshots,
  rejected_candidates,
  events_calendar,
  macro_snapshots,
  macro_releases,
  prediction_scores,
  backtest_runs,
  event_library,
  digests,
  job_runs,
  config,
  config_audit,
  ai_usage,
  ai_smoke_runs,
  regulatory_actions,
  calibrators,
  calibration_records: [],
  results_extracts: [],
  post_mortems: [],
  monthly_reports,
  suggestions,
  ai_vs_rules,
  golive_checks,
  market_regime,
  positioning_snapshots,
  research_runs,
  research_tool_calls,
  research_files,
  research_lessons,
  lab_hypotheses,
  lab_specs,
  lab_trials,
  lab_verdicts,
  lab_books,
};

export { premiumPath };

export class FixtureDb implements Db {
  readonly mode = "fixtures" as const;

  now(): Date {
    return FIXTURE_NOW;
  }

  async rows<T = Row>(table: string, q?: Query): Promise<T[]> {
    return applyQuery(TABLES[table] ?? [], q) as T[];
  }

  async setSwitch(key: string, value: boolean): Promise<void> {
    const row = config.find((r) => r.key === key);
    const old = row ? row.value_json : null;
    if (row) row.value_json = value;
    else config.push({ key, value_json: value });
    config_audit.push({
      id: config_audit.length + 1,
      ts: new Date().toISOString(),
      key,
      old_value: old,
      new_value: value,
      changed_by: "owner@example.com",
    });
  }

  async decideSuggestion(
    id: number,
    status: "accepted" | "rejected",
  ): Promise<void> {
    const row = suggestions.find((r) => r.id === id && r.status === "pending");
    if (row)
      Object.assign(row, {
        status,
        decided_at: new Date().toISOString(),
        decided_by: "owner@example.com",
      });
  }

  async deleteLesson(id: number): Promise<void> {
    const row = research_lessons.find((r) => r.id === id && !r.deleted);
    if (!row) return;
    Object.assign(row, { deleted: true, deleted_at: new Date().toISOString(), deleted_by: "owner@example.com" });
    config_audit.push({
      id: config_audit.length + 1,
      ts: new Date().toISOString(),
      key: `research.lesson.${id}`,
      old_value: { scope: `${row.scope_kind}:${row.scope}`, text: row.text },
      new_value: "deleted",
      changed_by: "owner@example.com",
    });
  }

  async setLabAlerts(bookId: number, enabled: boolean): Promise<void> {
    const row = lab_books.find((r) => r.id === bookId);
    if (!row) return;
    const old = row.alerts_enabled;
    row.alerts_enabled = enabled;
    config_audit.push({ id: config_audit.length + 1, ts: new Date().toISOString(), key: `lab.alerts.${row.book}`,
      old_value: old, new_value: enabled, changed_by: "owner@example.com" });
  }

  async userEmail(): Promise<string | null> {
    return "owner@example.com";
  }
}
