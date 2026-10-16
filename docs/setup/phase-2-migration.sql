BEGIN;

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

COMMIT;

