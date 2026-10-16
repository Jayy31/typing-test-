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

COMMIT;

