BEGIN;

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

CREATE INDEX ix_kg_paths_target ON kg_paths (target);

CREATE INDEX ix_kg_paths_source_news_id ON kg_paths (source_news_id);

ALTER TABLE kg_paths ENABLE ROW LEVEL SECURITY;

UPDATE alembic_version SET version_num='0011' WHERE alembic_version.version_num = '0010';

COMMIT;

