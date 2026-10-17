BEGIN;

-- Running upgrade 0011 -> 0012

CREATE TABLE dashboard_owner (
    email VARCHAR(256) NOT NULL, 
    PRIMARY KEY (email)
);

CREATE TABLE config_audit (
    id BIGSERIAL NOT NULL, 
    ts TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    key VARCHAR(128) NOT NULL, 
    old_value JSONB, 
    new_value JSONB, 
    changed_by VARCHAR(256), 
    PRIMARY KEY (id)
);

CREATE INDEX ix_config_audit_key ON config_audit (key);

CREATE TABLE event_library (
    id BIGSERIAL NOT NULL, 
    event_type VARCHAR(64) NOT NULL, 
    level_key VARCHAR(96) NOT NULL, 
    regime VARCHAR(16) NOT NULL, 
    "window" INTEGER NOT NULL, 
    n INTEGER NOT NULL, 
    n_eff FLOAT NOT NULL, 
    mean FLOAT NOT NULL, 
    median FLOAT NOT NULL, 
    hit_rate FLOAT NOT NULL, 
    p25 FLOAT NOT NULL, 
    p75 FLOAT NOT NULL, 
    examples_json JSONB, 
    computed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_event_library UNIQUE (event_type, level_key, regime, "window")
);

CREATE INDEX ix_event_library_event_type ON event_library (event_type);

ALTER TABLE event_library ENABLE ROW LEVEL SECURITY;

ALTER TABLE dashboard_owner ENABLE ROW LEVEL SECURITY;

ALTER TABLE config_audit ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    CREATE ROLE anon NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    CREATE ROLE authenticated NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'auth') THEN
    CREATE SCHEMA auth;
    -- stub of Supabase's auth.jwt(): claims from the request.jwt.claims setting
    CREATE FUNCTION auth.jwt() RETURNS jsonb LANGUAGE sql STABLE AS
      $f$ SELECT coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb $f$;
    GRANT USAGE ON SCHEMA auth TO anon, authenticated;
  END IF;
END $$;;

CREATE OR REPLACE FUNCTION public.dashboard_email() RETURNS text LANGUAGE sql STABLE AS
  $f$ SELECT lower(auth.jwt() ->> 'email') $f$;

CREATE OR REPLACE FUNCTION public.is_dashboard_owner() RETURNS boolean
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
  $f$ SELECT EXISTS (SELECT 1 FROM public.dashboard_owner o
                     WHERE lower(o.email) = public.dashboard_email()) $f$;

CREATE OR REPLACE FUNCTION public.is_owner_switch(k text) RETURNS boolean
  LANGUAGE sql IMMUTABLE AS
  $f$ SELECT left(k, 5) = 'feat.' OR k IN ('brief.morning_enabled', 'brief.evening_enabled', 'alerts_muted') $f$;

CREATE OR REPLACE FUNCTION public.log_owner_switch() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $f$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF public.is_owner_switch(OLD.key) THEN
      INSERT INTO public.config_audit (key, old_value, new_value, changed_by)
      VALUES (OLD.key, OLD.value_json, NULL, coalesce(public.dashboard_email(), current_user));
    END IF;
    RETURN OLD;
  END IF;
  IF public.is_owner_switch(NEW.key)
     AND (TG_OP = 'INSERT' OR OLD.value_json IS DISTINCT FROM NEW.value_json) THEN
    INSERT INTO public.config_audit (key, old_value, new_value, changed_by)
    VALUES (NEW.key, CASE WHEN TG_OP = 'UPDATE' THEN OLD.value_json END, NEW.value_json,
            coalesce(public.dashboard_email(), current_user));
  END IF;
  RETURN NEW;
END $f$;;

DO $$ DECLARE t text; BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public'
           AND tablename <> 'alembic_version' LOOP
    EXECUTE 'ALTER TABLE public.' || quote_ident(t) || ' ENABLE ROW LEVEL SECURITY';
    EXECUTE 'DROP POLICY IF EXISTS owner_read ON public.' || quote_ident(t);
    EXECUTE 'CREATE POLICY owner_read ON public.' || quote_ident(t)
         || ' FOR SELECT TO authenticated USING (public.is_dashboard_owner())';
    EXECUTE 'GRANT SELECT ON public.' || quote_ident(t) || ' TO authenticated';
  END LOOP;
END $$;;

CREATE POLICY owner_switch_insert ON public.config FOR INSERT TO authenticated
  WITH CHECK (public.is_dashboard_owner() AND public.is_owner_switch(key)
              AND jsonb_typeof(value_json) = 'boolean');
CREATE POLICY owner_switch_update ON public.config FOR UPDATE TO authenticated
  USING (public.is_dashboard_owner() AND public.is_owner_switch(key))
  WITH CHECK (public.is_dashboard_owner() AND public.is_owner_switch(key)
              AND jsonb_typeof(value_json) = 'boolean');
GRANT INSERT, UPDATE ON public.config TO authenticated;
CREATE TRIGGER config_owner_switch_audit AFTER INSERT OR UPDATE OR DELETE ON public.config
  FOR EACH ROW EXECUTE FUNCTION public.log_owner_switch();;

UPDATE alembic_version SET version_num='0012' WHERE alembic_version.version_num = '0011';

COMMIT;

