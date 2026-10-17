BEGIN;

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

COMMIT;

