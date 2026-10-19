export interface Signal {
  id: number;
  symbol: string;
  sector: string | null;
  direction: string;
  score: number;
  status: string;
  strike: number | null;
  expiry: string | null;
  option_type: string | null;
  pricing_status: string | null;
  expected_value: number | null;
  expected_value_inr: number | null;
  expected_value_raw?: number | null;
  p_t1_before_sl: number | null;
  p_t1_before_sl_raw?: number | null;
  p_move: number | null;
  predicted_move_pct: number | null;
  predicted_move_raw_pct: number | null;
  predicted_move_source: string | null;
  implied_move_pct: number | null;
  edge_ratio: number | null;
  edge_ratio_raw?: number | null;
  shrink_weight?: number | null;
  calibrator_id: number | null;
  calibration_method: string | null;
  impact_order: number | null;
  prediction_source: string | null;
  enter_after: string | null;
  created_at: string;
  updated_at: string;
  analysis_id: number | null;
  book?: string | null;
  theme_key?: string | null;
  engine_version?: string | null;
  iv_percentile?: number | null;
  news_to_signal_s?: number | null;
}

export interface Trade {
  id: number;
  signal_id: number;
  book?: string | null;
  symbol: string;
  option_type: string;
  strike: number;
  expiry: string;
  lots: number;
  lot_size: number;
  entry_ts: string;
  entry_premium: number;
  sl: number;
  t1: number;
  t2: number;
  t3: number;
  status: string;
  last_premium: number | null;
  last_ts: string | null;
  exit_ts: string | null;
  exit_premium: number | null;
  exit_reason: string | null;
  pnl: number | null;
  pnl_pct: number | null;
  fillability: string | null;
  entry_fill?: string | null;
  slippage_inr: number | null;
  booking_mode: string | null;
  attribution_json: Record<string, number> | null;
  state_json: {
    targets_hit?: number;
    stop?: number;
    fills?: { event?: string; fraction: number; price: number }[];
  } | null;
  theme_key?: string | null;
}

export interface TradeEvent {
  id: number;
  trade_id: number;
  ts: string;
  event: string;
  premium: number | null;
  lots: number | null;
  trigger_level: number | null;
  slippage_inr: number | null;
  gap: boolean | null;
  note: string | null;
}

export interface JobRun {
  id: number;
  job: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  error: string | null;
}
export interface NewsItem {
  id: number;
  source: string;
  title: string;
  body: string | null;
  url: string;
  published_at: string | null;
  fetched_at: string;
}
export interface NewsTag {
  id: number;
  news_id: number;
  symbol: string | null;
  sector: string | null;
  event_type: string | null;
  direction: string | null;
  sentiment: number | null;
  novelty: number | null;
}
export interface Rejected {
  id: number;
  ts: string;
  symbol: string;
  stage: string;
  reason: string;
  direction: string | null;
  score: number | null;
  outcome_status: string;
  option_return_pct: number | null;
  stock_return_pct?: number | null;
  resolved_at?: string | null;
  details_json: Record<string, unknown> | null;
}
export interface ConfigRow {
  key: string;
  value_json: unknown;
}
export interface Snapshot {
  ts: string;
  capital: number;
  realised_inr: number;
  unrealised_inr: number;
  equity: number;
  peak_equity: number;
  drawdown_pct: number;
  open_trades: number;
  premium_at_risk_inr: number;
  capital_used_inr: number | null;
  exposure_json: Record<string, number> | null;
}
export interface KgPath {
  id: number;
  source_news_id: number;
  source_symbol: string;
  target: string;
  path_key: string;
  hops: number;
  role: string;
  sign_basis: string;
  weight: number;
  sentiment: number | null;
  news_id: number | null;
  created_at: string;
}
