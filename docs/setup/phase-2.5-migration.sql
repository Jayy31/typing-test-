BEGIN;

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

COMMIT;

