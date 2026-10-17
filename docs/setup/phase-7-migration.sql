BEGIN;

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

CREATE INDEX ix_bulk_block_deals_disclosed_at ON bulk_block_deals (disclosed_at);

CREATE INDEX ix_bulk_block_deals_symbol ON bulk_block_deals (symbol);

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

CREATE INDEX ix_ratings_symbol ON ratings (symbol);

CREATE INDEX ix_ratings_disclosed_at ON ratings (disclosed_at);

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

COMMIT;

