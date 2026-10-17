BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 0001

CREATE TABLE ai_usage (
    id BIGSERIAL NOT NULL, 
    ts TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    model VARCHAR(128) NOT NULL, 
    input_tokens INTEGER NOT NULL, 
    output_tokens INTEGER NOT NULL, 
    cached_tokens INTEGER NOT NULL, 
    cost_inr FLOAT NOT NULL, 
    PRIMARY KEY (id)
);

CREATE TABLE config (
    key VARCHAR(128) NOT NULL, 
    value_json JSONB NOT NULL, 
    PRIMARY KEY (key)
);

CREATE TABLE events_calendar (
    id BIGSERIAL NOT NULL, 
    symbol VARCHAR(32), 
    sector VARCHAR(64), 
    event_type VARCHAR(64) NOT NULL, 
    event_date DATE NOT NULL, 
    source VARCHAR(64), 
    details JSONB, 
    PRIMARY KEY (id), 
    UNIQUE (symbol, sector, event_type, event_date)
);

CREATE INDEX ix_events_calendar_event_date ON events_calendar (event_date);

CREATE INDEX ix_events_calendar_symbol ON events_calendar (symbol);

CREATE TABLE instruments (
    symbol VARCHAR(32) NOT NULL, 
    name VARCHAR(128) NOT NULL, 
    sector VARCHAR(64) NOT NULL, 
    sub_sector VARCHAR(64), 
    aliases JSONB NOT NULL, 
    is_fno BOOLEAN NOT NULL, 
    lot_size INTEGER, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (symbol)
);

CREATE TABLE job_runs (
    id BIGSERIAL NOT NULL, 
    job VARCHAR(64) NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    finished_at TIMESTAMP WITH TIME ZONE, 
    status VARCHAR(16) NOT NULL, 
    error TEXT, 
    items_processed INTEGER, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_job_runs_job ON job_runs (job);

CREATE TABLE macro_snapshots (
    ts TIMESTAMP WITH TIME ZONE NOT NULL, 
    brent FLOAT, 
    usdinr FLOAT, 
    dxy FLOAT, 
    us10y FLOAT, 
    gift_nifty FLOAT, 
    india_vix FLOAT, 
    fii_net FLOAT, 
    dii_net FLOAT, 
    adr_json JSONB, 
    PRIMARY KEY (ts)
);

CREATE TABLE news_clusters (
    id BIGSERIAL NOT NULL, 
    headline TEXT NOT NULL, 
    first_seen TIMESTAMP WITH TIME ZONE NOT NULL, 
    last_seen TIMESTAMP WITH TIME ZONE NOT NULL, 
    item_count INTEGER NOT NULL, 
    PRIMARY KEY (id)
);

CREATE TABLE option_quotes (
    id BIGSERIAL NOT NULL, 
    ts TIMESTAMP WITH TIME ZONE NOT NULL, 
    symbol VARCHAR(32) NOT NULL, 
    expiry DATE NOT NULL, 
    strike FLOAT NOT NULL, 
    type VARCHAR(2) NOT NULL, 
    bid FLOAT, 
    ask FLOAT, 
    ltp FLOAT, 
    iv FLOAT, 
    oi BIGINT, 
    volume BIGINT, 
    spot FLOAT, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_option_quotes_lookup ON option_quotes (symbol, expiry, strike, ts);

CREATE TABLE rules (
    id BIGSERIAL NOT NULL, 
    name VARCHAR(128) NOT NULL, 
    condition_json JSONB NOT NULL, 
    effect_json JSONB NOT NULL, 
    enabled BOOLEAN NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (name)
);

CREATE TABLE analyses (
    id BIGSERIAL NOT NULL, 
    cluster_id BIGINT, 
    symbol VARCHAR(32) NOT NULL, 
    thesis TEXT NOT NULL, 
    direction VARCHAR(16) NOT NULL, 
    move_size FLOAT, 
    window_days INTEGER, 
    risks JSONB, 
    counter_args JSONB, 
    analogues JSONB, 
    scores_json JSONB, 
    confidence VARCHAR(16), 
    model VARCHAR(128), 
    prompt_ver VARCHAR(32), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(cluster_id) REFERENCES news_clusters (id)
);

CREATE INDEX ix_analyses_symbol ON analyses (symbol);

CREATE TABLE news_items (
    id BIGSERIAL NOT NULL, 
    url_hash VARCHAR(64) NOT NULL, 
    source VARCHAR(64) NOT NULL, 
    title TEXT NOT NULL, 
    body TEXT, 
    url TEXT NOT NULL, 
    published_at TIMESTAMP WITH TIME ZONE, 
    fetched_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    cluster_id BIGINT, 
    status VARCHAR(16) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(cluster_id) REFERENCES news_clusters (id), 
    UNIQUE (url_hash)
);

CREATE INDEX ix_news_items_status ON news_items (status);

CREATE TABLE news_tags (
    id BIGSERIAL NOT NULL, 
    news_id BIGINT NOT NULL, 
    symbol VARCHAR(32), 
    sector VARCHAR(64), 
    theme VARCHAR(64), 
    event_type VARCHAR(64), 
    direction VARCHAR(16), 
    sentiment FLOAT, 
    novelty FLOAT, 
    model VARCHAR(128), 
    prompt_ver VARCHAR(32), 
    PRIMARY KEY (id), 
    FOREIGN KEY(news_id) REFERENCES news_items (id)
);

CREATE INDEX ix_news_tags_news_id ON news_tags (news_id);

CREATE INDEX ix_news_tags_symbol ON news_tags (symbol);

CREATE TABLE signals (
    id BIGSERIAL NOT NULL, 
    symbol VARCHAR(32) NOT NULL, 
    sector VARCHAR(64), 
    direction VARCHAR(16) NOT NULL, 
    strike FLOAT, 
    expiry DATE, 
    score FLOAT NOT NULL, 
    status VARCHAR(24) NOT NULL, 
    analysis_id BIGINT, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(analysis_id) REFERENCES analyses (id)
);

CREATE INDEX ix_signals_status ON signals (status);

CREATE INDEX ix_signals_symbol ON signals (symbol);

CREATE TABLE paper_trades (
    id BIGSERIAL NOT NULL, 
    signal_id BIGINT NOT NULL, 
    entry_ts TIMESTAMP WITH TIME ZONE NOT NULL, 
    entry_premium FLOAT NOT NULL, 
    lots INTEGER NOT NULL, 
    sl FLOAT NOT NULL, 
    t1 FLOAT NOT NULL, 
    t2 FLOAT NOT NULL, 
    t3 FLOAT NOT NULL, 
    status VARCHAR(16) NOT NULL, 
    exit_ts TIMESTAMP WITH TIME ZONE, 
    exit_premium FLOAT, 
    pnl FLOAT, 
    exit_reason VARCHAR(32), 
    PRIMARY KEY (id), 
    FOREIGN KEY(signal_id) REFERENCES signals (id), 
    UNIQUE (signal_id)
);

CREATE INDEX ix_paper_trades_status ON paper_trades (status);

CREATE TABLE signal_versions (
    signal_id BIGINT NOT NULL, 
    version INTEGER NOT NULL, 
    score FLOAT NOT NULL, 
    status VARCHAR(24) NOT NULL, 
    reason TEXT, 
    ts TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (signal_id, version), 
    FOREIGN KEY(signal_id) REFERENCES signals (id)
);

CREATE TABLE trade_events (
    id BIGSERIAL NOT NULL, 
    trade_id BIGINT NOT NULL, 
    ts TIMESTAMP WITH TIME ZONE NOT NULL, 
    event VARCHAR(16) NOT NULL, 
    premium FLOAT, 
    note TEXT, 
    PRIMARY KEY (id), 
    FOREIGN KEY(trade_id) REFERENCES paper_trades (id), 
    UNIQUE (trade_id, event, ts)
);

CREATE INDEX ix_trade_events_trade_id ON trade_events (trade_id);

INSERT INTO alembic_version (version_num) VALUES ('0001') RETURNING alembic_version.version_num;

-- Running upgrade 0001 -> 0002

ALTER TABLE ai_usage ENABLE ROW LEVEL SECURITY;

ALTER TABLE analyses ENABLE ROW LEVEL SECURITY;

ALTER TABLE config ENABLE ROW LEVEL SECURITY;

ALTER TABLE events_calendar ENABLE ROW LEVEL SECURITY;

ALTER TABLE instruments ENABLE ROW LEVEL SECURITY;

ALTER TABLE job_runs ENABLE ROW LEVEL SECURITY;

ALTER TABLE macro_snapshots ENABLE ROW LEVEL SECURITY;

ALTER TABLE news_clusters ENABLE ROW LEVEL SECURITY;

ALTER TABLE news_items ENABLE ROW LEVEL SECURITY;

ALTER TABLE news_tags ENABLE ROW LEVEL SECURITY;

ALTER TABLE option_quotes ENABLE ROW LEVEL SECURITY;

ALTER TABLE paper_trades ENABLE ROW LEVEL SECURITY;

ALTER TABLE rules ENABLE ROW LEVEL SECURITY;

ALTER TABLE signal_versions ENABLE ROW LEVEL SECURITY;

ALTER TABLE signals ENABLE ROW LEVEL SECURITY;

ALTER TABLE trade_events ENABLE ROW LEVEL SECURITY;

UPDATE alembic_version SET version_num='0002' WHERE alembic_version.version_num = '0001';

-- Running upgrade 0002 -> 0003

CREATE TABLE alerts (
    id BIGSERIAL NOT NULL, 
    kind VARCHAR(16) NOT NULL, 
    dedupe_key VARCHAR(128) NOT NULL, 
    text TEXT NOT NULL, 
    signal_id BIGINT, 
    status VARCHAR(16) NOT NULL, 
    attempts INTEGER NOT NULL, 
    error TEXT, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    sent_at TIMESTAMP WITH TIME ZONE, 
    PRIMARY KEY (id), 
    FOREIGN KEY(signal_id) REFERENCES signals (id), 
    UNIQUE (dedupe_key)
);

CREATE INDEX ix_alerts_kind ON alerts (kind);

CREATE INDEX ix_alerts_status ON alerts (status);

ALTER TABLE alerts ENABLE ROW LEVEL SECURITY;

UPDATE alembic_version SET version_num='0003' WHERE alembic_version.version_num = '0002';

-- Running upgrade 0003 -> 0004

CREATE TABLE backtest_runs (
    id BIGSERIAL NOT NULL, 
    config_hash VARCHAR(64) NOT NULL, 
    config_json JSONB NOT NULL, 
    period_start DATE NOT NULL, 
    period_end DATE NOT NULL, 
    status VARCHAR(16) NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    finished_at TIMESTAMP WITH TIME ZONE, 
    metrics_json JSONB, 
    notes TEXT, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_backtest_runs_config_hash ON backtest_runs (config_hash);

CREATE TABLE calibrators (
    id BIGSERIAL NOT NULL, 
    target VARCHAR(32) NOT NULL, 
    method VARCHAR(16) NOT NULL, 
    version INTEGER NOT NULL, 
    params_json JSONB NOT NULL, 
    n_samples INTEGER NOT NULL, 
    brier FLOAT, 
    fitted_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    active BOOLEAN NOT NULL, 
    PRIMARY KEY (id)
);

CREATE TABLE kg_nodes (
    id BIGSERIAL NOT NULL, 
    kind VARCHAR(32) NOT NULL, 
    name VARCHAR(256) NOT NULL, 
    symbol VARCHAR(32), 
    attrs JSONB, 
    PRIMARY KEY (id), 
    UNIQUE (kind, name)
);

CREATE INDEX ix_kg_nodes_symbol ON kg_nodes (symbol);

CREATE TABLE backtest_trades (
    id BIGSERIAL NOT NULL, 
    run_id BIGINT NOT NULL, 
    symbol VARCHAR(32) NOT NULL, 
    event_ref VARCHAR(64), 
    direction VARCHAR(8) NOT NULL, 
    expiry DATE NOT NULL, 
    strike FLOAT NOT NULL, 
    entry_date DATE NOT NULL, 
    entry_premium FLOAT NOT NULL, 
    exit_date DATE, 
    exit_premium FLOAT, 
    exit_reason VARCHAR(32), 
    pnl_pct FLOAT, 
    pnl_inr FLOAT, 
    details_json JSONB, 
    PRIMARY KEY (id), 
    FOREIGN KEY(run_id) REFERENCES backtest_runs (id)
);

CREATE INDEX ix_backtest_trades_run_id ON backtest_trades (run_id);

CREATE TABLE kg_edges (
    id BIGSERIAL NOT NULL, 
    src BIGINT NOT NULL, 
    dst BIGINT NOT NULL, 
    type VARCHAR(32) NOT NULL, 
    weight FLOAT NOT NULL, 
    source TEXT, 
    confidence FLOAT, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(dst) REFERENCES kg_nodes (id), 
    FOREIGN KEY(src) REFERENCES kg_nodes (id), 
    UNIQUE (src, dst, type)
);

CREATE INDEX ix_kg_edges_dst ON kg_edges (dst);

CREATE INDEX ix_kg_edges_src ON kg_edges (src);

CREATE TABLE agent_runs (
    id BIGSERIAL NOT NULL, 
    analysis_id BIGINT NOT NULL, 
    agent VARCHAR(16) NOT NULL, 
    model VARCHAR(128) NOT NULL, 
    prompt_ver VARCHAR(32) NOT NULL, 
    input_refs JSONB, 
    output_json JSONB, 
    input_tokens INTEGER NOT NULL, 
    output_tokens INTEGER NOT NULL, 
    cost_inr FLOAT NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(analysis_id) REFERENCES analyses (id)
);

CREATE INDEX ix_agent_runs_analysis_id ON agent_runs (analysis_id);

CREATE TABLE scenarios (
    id BIGSERIAL NOT NULL, 
    analysis_id BIGINT NOT NULL, 
    name TEXT NOT NULL, 
    probability FLOAT NOT NULL, 
    stock_move_pct FLOAT NOT NULL, 
    timing_days INTEGER NOT NULL, 
    triggers JSONB, 
    invalidation TEXT, 
    PRIMARY KEY (id), 
    FOREIGN KEY(analysis_id) REFERENCES analyses (id)
);

CREATE INDEX ix_scenarios_analysis_id ON scenarios (analysis_id);

CREATE TABLE signal_candidates (
    id BIGSERIAL NOT NULL, 
    signal_id BIGINT NOT NULL, 
    ts TIMESTAMP WITH TIME ZONE NOT NULL, 
    expiry DATE NOT NULL, 
    strike FLOAT NOT NULL, 
    option_type VARCHAR(2) NOT NULL, 
    bid FLOAT, 
    ask FLOAT, 
    iv FLOAT, 
    oi BIGINT, 
    volume BIGINT, 
    spread_pct FLOAT, 
    ev_pct FLOAT, 
    ev_inr FLOAT, 
    p_t1_before_sl FLOAT, 
    p_t3 FLOAT, 
    max_loss_inr FLOAT, 
    exp_hold_days FLOAT, 
    theta_per_day FLOAT, 
    breakeven_move_pct FLOAT, 
    iv_sensitivity FLOAT, 
    chosen BOOLEAN NOT NULL, 
    rejected_reason TEXT, 
    details_json JSONB, 
    PRIMARY KEY (id), 
    FOREIGN KEY(signal_id) REFERENCES signals (id)
);

CREATE INDEX ix_signal_candidates_signal_id ON signal_candidates (signal_id);

CREATE TABLE calibration_records (
    id BIGSERIAL NOT NULL, 
    signal_id BIGINT, 
    trade_id BIGINT, 
    target VARCHAR(32) NOT NULL, 
    predicted FLOAT NOT NULL, 
    calibrator_id BIGINT, 
    config_hash VARCHAR(64), 
    outcome BOOLEAN, 
    recorded_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    resolved_at TIMESTAMP WITH TIME ZONE, 
    PRIMARY KEY (id), 
    FOREIGN KEY(calibrator_id) REFERENCES calibrators (id), 
    FOREIGN KEY(signal_id) REFERENCES signals (id), 
    FOREIGN KEY(trade_id) REFERENCES paper_trades (id)
);

CREATE INDEX ix_calibration_records_signal_id ON calibration_records (signal_id);

ALTER TABLE news_tags ADD COLUMN taxonomy_ver VARCHAR(16);

ALTER TABLE signals ADD COLUMN engine_version VARCHAR(16);

ALTER TABLE signals ADD COLUMN flags_json JSONB;

ALTER TABLE signals ADD COLUMN impact_order INTEGER DEFAULT '1' NOT NULL;

ALTER TABLE signals ADD COLUMN p_move FLOAT;

ALTER TABLE signals ADD COLUMN p_t1_before_sl FLOAT;

ALTER TABLE signals ADD COLUMN p_t2 FLOAT;

ALTER TABLE signals ADD COLUMN p_t3 FLOAT;

ALTER TABLE signals ADD COLUMN expected_value FLOAT;

ALTER TABLE signals ADD COLUMN expected_value_inr FLOAT;

ALTER TABLE signals ADD COLUMN calibrator_id BIGINT;

ALTER TABLE signals ADD COLUMN implied_move_pct FLOAT;

ALTER TABLE signals ADD COLUMN predicted_move_pct FLOAT;

ALTER TABLE signals ADD COLUMN predicted_move_source VARCHAR(16);

ALTER TABLE signals ADD COLUMN edge_ratio FLOAT;

ALTER TABLE signals ADD COLUMN iv_percentile FLOAT;

ALTER TABLE signals ADD COLUMN event_iv_premium FLOAT;

ALTER TABLE signals ADD CONSTRAINT fk_signals_calibrator_id FOREIGN KEY(calibrator_id) REFERENCES calibrators (id);

ALTER TABLE scenarios ENABLE ROW LEVEL SECURITY;

ALTER TABLE signal_candidates ENABLE ROW LEVEL SECURITY;

ALTER TABLE agent_runs ENABLE ROW LEVEL SECURITY;

ALTER TABLE calibrators ENABLE ROW LEVEL SECURITY;

ALTER TABLE calibration_records ENABLE ROW LEVEL SECURITY;

ALTER TABLE kg_nodes ENABLE ROW LEVEL SECURITY;

ALTER TABLE kg_edges ENABLE ROW LEVEL SECURITY;

ALTER TABLE backtest_runs ENABLE ROW LEVEL SECURITY;

ALTER TABLE backtest_trades ENABLE ROW LEVEL SECURITY;

UPDATE alembic_version SET version_num='0004' WHERE alembic_version.version_num = '0003';

-- Running upgrade 0004 -> 0005

CREATE TABLE rejected_candidates (
    id BIGSERIAL NOT NULL, 
    dedupe_key VARCHAR(128) NOT NULL, 
    ts TIMESTAMP WITH TIME ZONE NOT NULL, 
    symbol VARCHAR(32) NOT NULL, 
    stage VARCHAR(24) NOT NULL, 
    reason TEXT NOT NULL, 
    direction VARCHAR(8), 
    score FLOAT, 
    analysis_id BIGINT, 
    signal_id BIGINT, 
    spot_at_reject FLOAT, 
    predicted_move_pct FLOAT, 
    window_days INTEGER, 
    ref_expiry DATE, 
    ref_strike FLOAT, 
    ref_option_type VARCHAR(2), 
    ref_premium FLOAT, 
    details_json JSONB, 
    outcome_status VARCHAR(16) DEFAULT 'open' NOT NULL, 
    stock_return_pct FLOAT, 
    option_return_pct FLOAT, 
    exit_reason VARCHAR(16), 
    resolved_at TIMESTAMP WITH TIME ZONE, 
    PRIMARY KEY (id), 
    FOREIGN KEY(analysis_id) REFERENCES analyses (id), 
    FOREIGN KEY(signal_id) REFERENCES signals (id), 
    UNIQUE (dedupe_key)
);

CREATE INDEX ix_rejected_candidates_symbol ON rejected_candidates (symbol);

CREATE INDEX ix_rejected_candidates_ts ON rejected_candidates (ts);

ALTER TABLE instruments ADD COLUMN isin VARCHAR(16);

ALTER TABLE instruments ADD COLUMN instrument_key VARCHAR(64);

ALTER TABLE signals ADD COLUMN option_type VARCHAR(2);

ALTER TABLE signals ADD COLUMN pricing_status VARCHAR(16);

ALTER TABLE signals ADD COLUMN news_published_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE signals ADD COLUMN news_to_signal_s FLOAT;

ALTER TABLE signals ADD COLUMN premove_pct FLOAT;

ALTER TABLE signals ADD COLUMN data_age_json JSONB;

ALTER TABLE rejected_candidates ENABLE ROW LEVEL SECURITY;

UPDATE alembic_version SET version_num='0005' WHERE alembic_version.version_num = '0004';

-- Running upgrade 0005 -> 0006

CREATE TABLE portfolio_snapshots (
    ts TIMESTAMP WITH TIME ZONE NOT NULL, 
    capital FLOAT NOT NULL, 
    realised_inr FLOAT NOT NULL, 
    unrealised_inr FLOAT NOT NULL, 
    equity FLOAT NOT NULL, 
    peak_equity FLOAT NOT NULL, 
    drawdown_pct FLOAT NOT NULL, 
    open_trades INTEGER NOT NULL, 
    premium_at_risk_inr FLOAT NOT NULL, 
    exposure_json JSONB, 
    realised_fillable_inr FLOAT, 
    PRIMARY KEY (ts)
);

CREATE TABLE prediction_scores (
    id BIGSERIAL NOT NULL, 
    analysis_id BIGINT NOT NULL, 
    signal_id BIGINT, 
    symbol VARCHAR(32) NOT NULL, 
    direction VARCHAR(8) NOT NULL, 
    predicted_move_pct FLOAT NOT NULL, 
    predicted_move_raw_pct FLOAT, 
    predicted_source VARCHAR(16), 
    p_move FLOAT, 
    window_days INTEGER NOT NULL, 
    spot_start FLOAT NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    path_json JSONB, 
    status VARCHAR(12) DEFAULT 'open' NOT NULL, 
    realised_move_pct FLOAT, 
    direction_hit BOOLEAN, 
    move_error_pct FLOAT, 
    days_to_half_move INTEGER, 
    timing_error_days INTEGER, 
    max_favourable_pct FLOAT, 
    max_adverse_pct FLOAT, 
    scored_at TIMESTAMP WITH TIME ZONE, 
    PRIMARY KEY (id), 
    FOREIGN KEY(analysis_id) REFERENCES analyses (id), 
    FOREIGN KEY(signal_id) REFERENCES signals (id), 
    UNIQUE (analysis_id)
);

CREATE INDEX ix_prediction_scores_status ON prediction_scores (status);

CREATE INDEX ix_prediction_scores_symbol ON prediction_scores (symbol);

ALTER TABLE option_quotes ADD COLUMN bid_qty BIGINT;

ALTER TABLE option_quotes ADD COLUMN ask_qty BIGINT;

ALTER TABLE paper_trades ADD COLUMN symbol VARCHAR(32);

ALTER TABLE paper_trades ADD COLUMN option_type VARCHAR(2);

ALTER TABLE paper_trades ADD COLUMN strike FLOAT;

ALTER TABLE paper_trades ADD COLUMN expiry DATE;

ALTER TABLE paper_trades ADD COLUMN lot_size INTEGER;

ALTER TABLE paper_trades ADD COLUMN theme_key VARCHAR(64);

ALTER TABLE paper_trades ADD COLUMN rules_hash VARCHAR(16);

ALTER TABLE paper_trades ADD COLUMN rules_json JSONB;

ALTER TABLE paper_trades ADD COLUMN state_json JSONB;

ALTER TABLE paper_trades ADD COLUMN last_premium FLOAT;

ALTER TABLE paper_trades ADD COLUMN last_spot FLOAT;

ALTER TABLE paper_trades ADD COLUMN last_ts TIMESTAMP WITH TIME ZONE;

ALTER TABLE paper_trades ADD COLUMN pnl_pct FLOAT;

ALTER TABLE paper_trades ADD COLUMN entry_bid FLOAT;

ALTER TABLE paper_trades ADD COLUMN entry_ask FLOAT;

ALTER TABLE paper_trades ADD COLUMN entry_bid_qty BIGINT;

ALTER TABLE paper_trades ADD COLUMN entry_ask_qty BIGINT;

ALTER TABLE paper_trades ADD COLUMN exit_bid_qty BIGINT;

ALTER TABLE paper_trades ADD COLUMN entry_fill VARCHAR(12);

ALTER TABLE paper_trades ADD COLUMN exit_fill VARCHAR(12);

ALTER TABLE paper_trades ADD COLUMN fillability VARCHAR(12);

ALTER TABLE paper_trades ADD COLUMN spot_entry FLOAT;

ALTER TABLE paper_trades ADD COLUMN spot_exit FLOAT;

ALTER TABLE paper_trades ADD COLUMN pre_news_spot FLOAT;

ALTER TABLE paper_trades ADD COLUMN benchmark_stock_pct FLOAT;

ALTER TABLE paper_trades ADD COLUMN benchmark_stock_inr FLOAT;

ALTER TABLE paper_trades ADD COLUMN mfe_pct FLOAT;

ALTER TABLE paper_trades ADD COLUMN mae_pct FLOAT;

ALTER TABLE paper_trades ADD COLUMN attribution_json JSONB;

ALTER TABLE paper_trades ADD COLUMN review_flag TEXT;

CREATE INDEX ix_paper_trades_fillability ON paper_trades (fillability);

CREATE INDEX ix_paper_trades_symbol ON paper_trades (symbol);

CREATE INDEX ix_paper_trades_theme_key ON paper_trades (theme_key);

ALTER TABLE signals ADD COLUMN shrink_weight FLOAT;

ALTER TABLE signals ADD COLUMN predicted_move_raw_pct FLOAT;

ALTER TABLE signals ADD COLUMN edge_ratio_raw FLOAT;

ALTER TABLE signals ADD COLUMN expected_value_raw FLOAT;

ALTER TABLE signals ADD COLUMN p_t1_before_sl_raw FLOAT;

ALTER TABLE signals ADD COLUMN theme_key VARCHAR(64);

CREATE INDEX ix_signals_theme_key ON signals (theme_key);

ALTER TABLE trade_events ADD COLUMN fraction FLOAT;

ALTER TABLE trade_events ADD COLUMN spot FLOAT;

ALTER TABLE trade_events ADD COLUMN bid_qty BIGINT;

ALTER TABLE trade_events ADD COLUMN ask_qty BIGINT;

ALTER TABLE portfolio_snapshots ENABLE ROW LEVEL SECURITY;

ALTER TABLE prediction_scores ENABLE ROW LEVEL SECURITY;

UPDATE alembic_version SET version_num='0006' WHERE alembic_version.version_num = '0005';

-- Running upgrade 0006 -> 0007

ALTER TABLE paper_trades ADD COLUMN booking_mode VARCHAR(16);

ALTER TABLE paper_trades ADD COLUMN slippage_inr FLOAT;

ALTER TABLE portfolio_snapshots ADD COLUMN capital_used_inr FLOAT;

ALTER TABLE trade_events ADD COLUMN lots INTEGER;

ALTER TABLE trade_events ADD COLUMN trigger_level FLOAT;

ALTER TABLE trade_events ADD COLUMN slippage_inr FLOAT;

ALTER TABLE trade_events ADD COLUMN gap BOOLEAN;

UPDATE alembic_version SET version_num='0007' WHERE alembic_version.version_num = '0006';

-- Running upgrade 0007 -> 0008

ALTER TABLE analyses ADD COLUMN prediction_source VARCHAR(8);

ALTER TABLE signals ADD COLUMN prediction_source VARCHAR(8);

ALTER TABLE signals ADD COLUMN calibration_method VARCHAR(16);

ALTER TABLE signals ADD COLUMN ai_weight FLOAT;

ALTER TABLE prediction_scores ADD COLUMN prediction_source VARCHAR(8);

ALTER TABLE calibrators ADD COLUMN source VARCHAR(8) DEFAULT 'all' NOT NULL;

ALTER TABLE calibrators ADD COLUMN n_events INTEGER;

ALTER TABLE calibration_records ADD COLUMN analysis_id BIGINT;

ALTER TABLE calibration_records ADD COLUMN rejection_id BIGINT;

ALTER TABLE calibration_records ADD COLUMN symbol VARCHAR(32);

ALTER TABLE calibration_records ADD COLUMN source VARCHAR(8);

ALTER TABLE calibration_records ADD COLUMN cluster_key VARCHAR(64);

ALTER TABLE calibration_records ADD COLUMN predicted_used FLOAT;

ALTER TABLE calibration_records ADD CONSTRAINT fk_calibration_records_analysis_id FOREIGN KEY(analysis_id) REFERENCES analyses (id);

ALTER TABLE calibration_records ADD CONSTRAINT fk_calibration_records_rejection_id FOREIGN KEY(rejection_id) REFERENCES rejected_candidates (id);

CREATE INDEX ix_calibration_records_analysis_id ON calibration_records (analysis_id);

CREATE INDEX ix_calibration_records_cluster_key ON calibration_records (cluster_key);

CREATE TABLE post_mortems (
    id BIGSERIAL NOT NULL, 
    trade_id BIGINT NOT NULL, 
    analysis_id BIGINT, 
    prediction_source VARCHAR(8), 
    primary_cause VARCHAR(24) NOT NULL, 
    secondary_causes JSONB, 
    thesis_correct BOOLEAN, 
    summary TEXT NOT NULL, 
    output_json JSONB NOT NULL, 
    model VARCHAR(128) NOT NULL, 
    prompt_ver VARCHAR(32) NOT NULL, 
    cost_inr FLOAT NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_post_mortems_trade_id UNIQUE (trade_id), 
    FOREIGN KEY(trade_id) REFERENCES paper_trades (id), 
    FOREIGN KEY(analysis_id) REFERENCES analyses (id)
);

CREATE INDEX ix_post_mortems_primary_cause ON post_mortems (primary_cause);

CREATE TABLE digests (
    id BIGSERIAL NOT NULL, 
    week_start DATE NOT NULL, 
    text TEXT NOT NULL, 
    data_json JSONB NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_digests_week_start UNIQUE (week_start)
);

ALTER TABLE post_mortems ENABLE ROW LEVEL SECURITY;

ALTER TABLE digests ENABLE ROW LEVEL SECURITY;

UPDATE alembic_version SET version_num='0008' WHERE alembic_version.version_num = '0007';

-- Running upgrade 0008 -> 0009

CREATE TABLE ai_smoke_runs (
    id BIGSERIAL NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    finished_at TIMESTAMP WITH TIME ZONE, 
    trigger VARCHAR(16) NOT NULL, 
    passed BOOLEAN NOT NULL, 
    total_cost_inr FLOAT NOT NULL, 
    panel_seconds FLOAT, 
    results_json JSONB NOT NULL, 
    fixes_json JSONB, 
    PRIMARY KEY (id)
);

ALTER TABLE ai_smoke_runs ENABLE ROW LEVEL SECURITY;

UPDATE alembic_version SET version_num='0009' WHERE alembic_version.version_num = '0008';

-- Running upgrade 0009 -> 0010

ALTER TABLE signals ADD COLUMN enter_after TIMESTAMP WITH TIME ZONE;

ALTER TABLE macro_snapshots ADD COLUMN commodities_json JSONB;

CREATE TABLE insider_trades (
    id BIGSERIAL NOT NULL, 
    ref VARCHAR(64) NOT NULL, 
    symbol VARCHAR(32) NOT NULL, 
    person TEXT, 
    person_category VARCHAR(64), 
    counterparty VARCHAR(16) NOT NULL, 
    side VARCHAR(4) NOT NULL, 
    quantity FLOAT, 
    value_inr FLOAT, 
    mode VARCHAR(64), 
    event_date DATE, 
    disclosed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    subtype VARCHAR(64), 
    material BOOLEAN DEFAULT false NOT NULL, 
    materiality_json JSONB, 
    news_id BIGINT, 
    raw_json JSONB, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_insider_trades_ref UNIQUE (ref), 
    FOREIGN KEY(news_id) REFERENCES news_items (id)
);

CREATE INDEX ix_insider_trades_symbol ON insider_trades (symbol);

CREATE INDEX ix_insider_trades_disclosed_at ON insider_trades (disclosed_at);

CREATE TABLE bulk_block_deals (
    id BIGSERIAL NOT NULL, 
    ref VARCHAR(64) NOT NULL, 
    kind VARCHAR(8) NOT NULL, 
    symbol VARCHAR(32) NOT NULL, 
    client TEXT, 
    counterparty VARCHAR(16) NOT NULL, 
    side VARCHAR(4) NOT NULL, 
    quantity FLOAT, 
    price FLOAT, 
    value_inr FLOAT, 
    event_date DATE, 
    disclosed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    subtype VARCHAR(64), 
    material BOOLEAN DEFAULT false NOT NULL, 
    materiality_json JSONB, 
    news_id BIGINT, 
    raw_json JSONB, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_bulk_block_deals_ref UNIQUE (ref), 
    FOREIGN KEY(news_id) REFERENCES news_items (id)
);

CREATE INDEX ix_bulk_block_deals_symbol ON bulk_block_deals (symbol);

CREATE INDEX ix_bulk_block_deals_disclosed_at ON bulk_block_deals (disclosed_at);

CREATE TABLE shareholding (
    id BIGSERIAL NOT NULL, 
    symbol VARCHAR(32) NOT NULL, 
    event_date DATE NOT NULL, 
    disclosed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    promoter_pct FLOAT, 
    fii_pct FLOAT, 
    dii_pct FLOAT, 
    public_pct FLOAT, 
    pledge_pct FLOAT, 
    subtypes JSONB, 
    subtype VARCHAR(64), 
    material BOOLEAN DEFAULT false NOT NULL, 
    materiality_json JSONB, 
    news_id BIGINT, 
    raw_json JSONB, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_shareholding UNIQUE (symbol, event_date), 
    FOREIGN KEY(news_id) REFERENCES news_items (id)
);

CREATE INDEX ix_shareholding_symbol ON shareholding (symbol);

CREATE INDEX ix_shareholding_disclosed_at ON shareholding (disclosed_at);

CREATE TABLE ratings (
    id BIGSERIAL NOT NULL, 
    ref VARCHAR(64) NOT NULL, 
    symbol VARCHAR(32) NOT NULL, 
    agency VARCHAR(32), 
    action VARCHAR(16) NOT NULL, 
    event_date DATE, 
    disclosed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    title TEXT NOT NULL, 
    url TEXT, 
    subtype VARCHAR(64), 
    material BOOLEAN DEFAULT false NOT NULL, 
    materiality_json JSONB, 
    news_id BIGINT, 
    raw_json JSONB, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_ratings_ref UNIQUE (ref), 
    FOREIGN KEY(news_id) REFERENCES news_items (id)
);

CREATE INDEX ix_ratings_disclosed_at ON ratings (disclosed_at);

CREATE INDEX ix_ratings_symbol ON ratings (symbol);

CREATE TABLE regulatory_actions (
    id BIGSERIAL NOT NULL, 
    ref VARCHAR(64) NOT NULL, 
    regulator VARCHAR(16) NOT NULL, 
    action_type VARCHAR(64), 
    symbol VARCHAR(32), 
    event_date DATE, 
    disclosed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    title TEXT NOT NULL, 
    url TEXT, 
    subtype VARCHAR(64), 
    material BOOLEAN DEFAULT false NOT NULL, 
    materiality_json JSONB, 
    news_id BIGINT, 
    raw_json JSONB, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_regulatory_actions_ref UNIQUE (ref), 
    FOREIGN KEY(news_id) REFERENCES news_items (id)
);

CREATE INDEX ix_regulatory_actions_symbol ON regulatory_actions (symbol);

CREATE INDEX ix_regulatory_actions_disclosed_at ON regulatory_actions (disclosed_at);

CREATE TABLE results_extracts (
    id BIGSERIAL NOT NULL, 
    symbol VARCHAR(32) NOT NULL, 
    period_end DATE NOT NULL, 
    disclosed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    method VARCHAR(8) NOT NULL, 
    consolidated BOOLEAN, 
    revenue FLOAT, 
    pat FLOAT, 
    ebitda FLOAT, 
    revenue_yoy_pct FLOAT, 
    pat_yoy_pct FLOAT, 
    margin_change_pp FLOAT, 
    guidance VARCHAR(16), 
    surprise_score FLOAT, 
    surprise_json JSONB, 
    model VARCHAR(128), 
    prompt_ver VARCHAR(32), 
    source_url TEXT, 
    latency_s FLOAT, 
    subtype VARCHAR(64), 
    material BOOLEAN DEFAULT false NOT NULL, 
    materiality_json JSONB, 
    news_id BIGINT, 
    raw_json JSONB, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_results_extracts UNIQUE (symbol, period_end), 
    FOREIGN KEY(news_id) REFERENCES news_items (id)
);

CREATE INDEX ix_results_extracts_disclosed_at ON results_extracts (disclosed_at);

CREATE INDEX ix_results_extracts_symbol ON results_extracts (symbol);

CREATE TABLE business_updates (
    id BIGSERIAL NOT NULL, 
    symbol VARCHAR(32) NOT NULL, 
    period DATE NOT NULL, 
    metric VARCHAR(32) NOT NULL, 
    value FLOAT, 
    yoy_pct FLOAT, 
    disclosed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    model VARCHAR(128), 
    prompt_ver VARCHAR(32), 
    subtype VARCHAR(64), 
    material BOOLEAN DEFAULT false NOT NULL, 
    materiality_json JSONB, 
    news_id BIGINT, 
    raw_json JSONB, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_business_updates UNIQUE (symbol, period, metric), 
    FOREIGN KEY(news_id) REFERENCES news_items (id)
);

CREATE INDEX ix_business_updates_disclosed_at ON business_updates (disclosed_at);

CREATE INDEX ix_business_updates_symbol ON business_updates (symbol);

CREATE TABLE macro_releases (
    id BIGSERIAL NOT NULL, 
    name VARCHAR(32) NOT NULL, 
    period DATE NOT NULL, 
    value FLOAT, 
    prior FLOAT, 
    released_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    source VARCHAR(32), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_macro_releases UNIQUE (name, period)
);

CREATE TABLE stock_stats (
    symbol VARCHAR(32) NOT NULL, 
    adv20_inr FLOAT, 
    issued_shares FLOAT, 
    close FLOAT, 
    mcap_inr FLOAT, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (symbol)
);

ALTER TABLE insider_trades ENABLE ROW LEVEL SECURITY;

ALTER TABLE bulk_block_deals ENABLE ROW LEVEL SECURITY;

ALTER TABLE shareholding ENABLE ROW LEVEL SECURITY;

ALTER TABLE ratings ENABLE ROW LEVEL SECURITY;

ALTER TABLE regulatory_actions ENABLE ROW LEVEL SECURITY;

ALTER TABLE results_extracts ENABLE ROW LEVEL SECURITY;

ALTER TABLE business_updates ENABLE ROW LEVEL SECURITY;

ALTER TABLE macro_releases ENABLE ROW LEVEL SECURITY;

ALTER TABLE stock_stats ENABLE ROW LEVEL SECURITY;

UPDATE alembic_version SET version_num='0010' WHERE alembic_version.version_num = '0009';

-- Running upgrade 0010 -> 0011

ALTER TABLE kg_edges ADD COLUMN exposure_kind VARCHAR(24);

ALTER TABLE kg_edges ADD COLUMN exposure_pct FLOAT;

ALTER TABLE kg_edges ADD COLUMN valid_from DATE;

ALTER TABLE kg_edges ADD COLUMN valid_to DATE;

ALTER TABLE kg_edges ADD COLUMN origin VARCHAR(16);

ALTER TABLE kg_edges ADD COLUMN evidence_news_id BIGINT;

ALTER TABLE kg_edges ADD FOREIGN KEY(evidence_news_id) REFERENCES news_items (id);

ALTER TABLE analyses ADD COLUMN impact_order INTEGER;

ALTER TABLE regulatory_actions ADD COLUMN match_confidence FLOAT;

ALTER TABLE regulatory_actions ADD COLUMN match_candidates JSONB;

CREATE TABLE kg_paths (
    id BIGSERIAL NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    source_news_id BIGINT NOT NULL, 
    source_symbol VARCHAR(32) NOT NULL, 
    target VARCHAR(32) NOT NULL, 
    path_key VARCHAR(256) NOT NULL, 
    hops INTEGER NOT NULL, 
    role VARCHAR(12) NOT NULL, 
    sign_basis VARCHAR(12) NOT NULL, 
    weight FLOAT NOT NULL, 
    sentiment FLOAT, 
    news_id BIGINT, 
    detail_json JSONB, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_kg_paths UNIQUE (source_news_id, target, path_key), 
    FOREIGN KEY(source_news_id) REFERENCES news_items (id), 
    FOREIGN KEY(news_id) REFERENCES news_items (id)
);

CREATE INDEX ix_kg_paths_source_news_id ON kg_paths (source_news_id);

CREATE INDEX ix_kg_paths_target ON kg_paths (target);

ALTER TABLE kg_paths ENABLE ROW LEVEL SECURITY;

UPDATE alembic_version SET version_num='0011' WHERE alembic_version.version_num = '0010';

-- Running upgrade 0011 -> 0012

CREATE TABLE dashboard_owner (
    email VARCHAR(256) NOT NULL, 
    PRIMARY KEY (email)
);

CREATE TABLE config_audit (
    id BIGSERIAL NOT NULL, 
    ts TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    key VARCHAR(128) NOT NULL, 
    old_value JSONB, 
    new_value JSONB, 
    changed_by VARCHAR(256), 
    PRIMARY KEY (id)
);

CREATE INDEX ix_config_audit_key ON config_audit (key);

CREATE TABLE event_library (
    id BIGSERIAL NOT NULL, 
    event_type VARCHAR(64) NOT NULL, 
    level_key VARCHAR(96) NOT NULL, 
    regime VARCHAR(16) NOT NULL, 
    "window" INTEGER NOT NULL, 
    n INTEGER NOT NULL, 
    n_eff FLOAT NOT NULL, 
    mean FLOAT NOT NULL, 
    median FLOAT NOT NULL, 
    hit_rate FLOAT NOT NULL, 
    p25 FLOAT NOT NULL, 
    p75 FLOAT NOT NULL, 
    examples_json JSONB, 
    computed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_event_library UNIQUE (event_type, level_key, regime, "window")
);

CREATE INDEX ix_event_library_event_type ON event_library (event_type);

ALTER TABLE event_library ENABLE ROW LEVEL SECURITY;

ALTER TABLE dashboard_owner ENABLE ROW LEVEL SECURITY;

ALTER TABLE config_audit ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    CREATE ROLE anon NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    CREATE ROLE authenticated NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'auth') THEN
    CREATE SCHEMA auth;
    -- stub of Supabase's auth.jwt(): claims from the request.jwt.claims setting
    CREATE FUNCTION auth.jwt() RETURNS jsonb LANGUAGE sql STABLE AS
      $f$ SELECT coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb $f$;
    GRANT USAGE ON SCHEMA auth TO anon, authenticated;
  END IF;
END $$;;

CREATE OR REPLACE FUNCTION public.dashboard_email() RETURNS text LANGUAGE sql STABLE AS
  $f$ SELECT lower(auth.jwt() ->> 'email') $f$;

CREATE OR REPLACE FUNCTION public.is_dashboard_owner() RETURNS boolean
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
  $f$ SELECT EXISTS (SELECT 1 FROM public.dashboard_owner o
                     WHERE lower(o.email) = public.dashboard_email()) $f$;

CREATE OR REPLACE FUNCTION public.is_owner_switch(k text) RETURNS boolean
  LANGUAGE sql IMMUTABLE AS
  $f$ SELECT left(k, 5) = 'feat.' OR k IN ('brief.morning_enabled', 'brief.evening_enabled', 'alerts_muted') $f$;

CREATE OR REPLACE FUNCTION public.log_owner_switch() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $f$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.is_owner_switch(OLD.key) THEN
      INSERT INTO public.config_audit (key, old_value, new_value, changed_by)
      VALUES (OLD.key, OLD.value_json, NULL, coalesce(public.dashboard_email(), current_user));
    END IF;
    RETURN OLD;
  END IF;
  IF public.is_owner_switch(NEW.key)
     AND (TG_OP = 'INSERT' OR OLD.value_json IS DISTINCT FROM NEW.value_json) THEN
    INSERT INTO public.config_audit (key, old_value, new_value, changed_by)
    VALUES (NEW.key, CASE WHEN TG_OP = 'UPDATE' THEN OLD.value_json END, NEW.value_json,
            coalesce(public.dashboard_email(), current_user));
  END IF;
  RETURN NEW;
END $f$;;

DO $$ DECLARE t text; BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public'
           AND tablename <> 'alembic_version' LOOP
    EXECUTE 'ALTER TABLE public.' || quote_ident(t) || ' ENABLE ROW LEVEL SECURITY';
    EXECUTE 'DROP POLICY IF EXISTS owner_read ON public.' || quote_ident(t);
    EXECUTE 'CREATE POLICY owner_read ON public.' || quote_ident(t)
         || ' FOR SELECT TO authenticated USING (public.is_dashboard_owner())';
    EXECUTE 'GRANT SELECT ON public.' || quote_ident(t) || ' TO authenticated';
  END LOOP;
END $$;;

CREATE POLICY owner_switch_insert ON public.config FOR INSERT TO authenticated
  WITH CHECK (public.is_dashboard_owner() AND public.is_owner_switch(key)
              AND jsonb_typeof(value_json) = 'boolean');
CREATE POLICY owner_switch_update ON public.config FOR UPDATE TO authenticated
  USING (public.is_dashboard_owner() AND public.is_owner_switch(key))
  WITH CHECK (public.is_dashboard_owner() AND public.is_owner_switch(key)
              AND jsonb_typeof(value_json) = 'boolean');
GRANT INSERT, UPDATE ON public.config TO authenticated;
CREATE TRIGGER config_owner_switch_audit AFTER INSERT OR UPDATE OR DELETE ON public.config
  FOR EACH ROW EXECUTE FUNCTION public.log_owner_switch();;

UPDATE alembic_version SET version_num='0012' WHERE alembic_version.version_num = '0011';

-- Running upgrade 0012 -> 0013

ALTER TABLE alerts ADD COLUMN file_path TEXT;

CREATE TABLE monthly_reports (
    id BIGSERIAL NOT NULL, 
    month DATE NOT NULL, 
    data_json JSONB NOT NULL, 
    text TEXT NOT NULL, 
    ai_summary TEXT, 
    ai_summary_status VARCHAR(24), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (month)
);

CREATE TABLE suggestions (
    id BIGSERIAL NOT NULL, 
    month DATE NOT NULL, 
    kind VARCHAR(8) NOT NULL, 
    param VARCHAR(64) NOT NULL, 
    file VARCHAR(128) NOT NULL, 
    yaml_path VARCHAR(128) NOT NULL, 
    current_value FLOAT NOT NULL, 
    proposed_value FLOAT NOT NULL, 
    expected_effect FLOAT NOT NULL, 
    evidence_json JSONB NOT NULL, 
    status VARCHAR(12) NOT NULL, 
    decided_at TIMESTAMP WITH TIME ZONE, 
    decided_by VARCHAR(256), 
    pr_url TEXT, 
    pr_number INTEGER, 
    applied_at TIMESTAMP WITH TIME ZONE, 
    review_json JSONB, 
    reverts_id BIGINT, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_suggestions UNIQUE (month, param, kind), 
    FOREIGN KEY(reverts_id) REFERENCES suggestions (id)
);

CREATE INDEX ix_suggestions_month ON suggestions (month);

CREATE INDEX ix_suggestions_status ON suggestions (status);

CREATE TABLE ai_vs_rules (
    id BIGSERIAL NOT NULL, 
    analysis_id BIGINT NOT NULL, 
    symbol VARCHAR(32) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    news_direction VARCHAR(8) NOT NULL, 
    ai_direction VARCHAR(8), 
    ai_signal BOOLEAN DEFAULT false NOT NULL, 
    rules_direction VARCHAR(8), 
    rules_reason TEXT, 
    analogue_n INTEGER, 
    analogue_mean FLOAT, 
    realised_move_pct FLOAT, 
    resolved_at TIMESTAMP WITH TIME ZONE, 
    PRIMARY KEY (id), 
    UNIQUE (analysis_id), 
    FOREIGN KEY(analysis_id) REFERENCES analyses (id)
);

CREATE INDEX ix_ai_vs_rules_symbol ON ai_vs_rules (symbol);

ALTER TABLE monthly_reports ENABLE ROW LEVEL SECURITY;

ALTER TABLE suggestions ENABLE ROW LEVEL SECURITY;

ALTER TABLE ai_vs_rules ENABLE ROW LEVEL SECURITY;

CREATE POLICY owner_read ON public.monthly_reports FOR SELECT TO authenticated USING (public.is_dashboard_owner());

GRANT SELECT ON public.monthly_reports TO authenticated;

CREATE POLICY owner_read ON public.suggestions FOR SELECT TO authenticated USING (public.is_dashboard_owner());

GRANT SELECT ON public.suggestions TO authenticated;

CREATE POLICY owner_read ON public.ai_vs_rules FOR SELECT TO authenticated USING (public.is_dashboard_owner());

GRANT SELECT ON public.ai_vs_rules TO authenticated;

CREATE POLICY owner_decide ON public.suggestions FOR UPDATE TO authenticated
  USING (public.is_dashboard_owner() AND status = 'pending')
  WITH CHECK (public.is_dashboard_owner() AND status IN ('accepted', 'rejected'));
GRANT UPDATE (status) ON public.suggestions TO authenticated;

CREATE OR REPLACE FUNCTION public.stamp_suggestion_decision() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $f$
BEGIN
  IF NEW.status IS DISTINCT FROM OLD.status AND NEW.status IN ('accepted', 'rejected')
     AND OLD.status = 'pending' THEN
    NEW.decided_at := now();
    NEW.decided_by := coalesce(public.dashboard_email(), current_user);
  END IF;
  RETURN NEW;
END $f$;
CREATE TRIGGER suggestion_decision BEFORE UPDATE ON public.suggestions
  FOR EACH ROW EXECUTE FUNCTION public.stamp_suggestion_decision();;

UPDATE alembic_version SET version_num='0013' WHERE alembic_version.version_num = '0012';

COMMIT;

