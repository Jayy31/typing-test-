BEGIN;

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

