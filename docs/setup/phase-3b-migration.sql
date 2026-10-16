BEGIN;

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

COMMIT;

