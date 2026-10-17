BEGIN;

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

COMMIT;

