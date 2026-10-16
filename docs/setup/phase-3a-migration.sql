BEGIN;

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

