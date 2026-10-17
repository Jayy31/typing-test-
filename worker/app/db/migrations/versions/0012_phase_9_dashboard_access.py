"""phase 9 dashboard access: owner-only read policies, owner switches, config audit

The dashboard uses the Supabase anon key + Supabase Auth. Every table gets a SELECT policy for
the `authenticated` role limited to the email in `dashboard_owner`. The only write the dashboard
may do is INSERT/UPDATE on `config` for owner-switch keys (feat.*, brief.morning_enabled,
brief.evening_enabled, alerts_muted) with a boolean value; a trigger logs every switch change to
`config_audit` (who, when, old -> new). The worker's role owns the tables and bypasses RLS.

On a plain Postgres (local tests) a stub `auth.jwt()` and the anon/authenticated roles are created
if missing; on Supabase the real ones exist and are left alone.

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
SWITCH_KEYS = ("brief.morning_enabled", "brief.evening_enabled", "alerts_muted")

STUBS = """
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
END $$;
"""

FUNCTIONS = f"""
CREATE OR REPLACE FUNCTION public.dashboard_email() RETURNS text LANGUAGE sql STABLE AS
  $f$ SELECT lower(auth.jwt() ->> 'email') $f$;

CREATE OR REPLACE FUNCTION public.is_dashboard_owner() RETURNS boolean
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
  $f$ SELECT EXISTS (SELECT 1 FROM public.dashboard_owner o
                     WHERE lower(o.email) = public.dashboard_email()) $f$;

CREATE OR REPLACE FUNCTION public.is_owner_switch(k text) RETURNS boolean
  LANGUAGE sql IMMUTABLE AS
  $f$ SELECT k LIKE 'feat.%' OR k IN ({", ".join(f"'{k}'" for k in SWITCH_KEYS)}) $f$;

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
END $f$;
"""


def upgrade() -> None:
    op.create_table(
        "dashboard_owner",
        sa.Column("email", sa.String(256), primary_key=True),
    )
    op.create_table(
        "config_audit",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("key", sa.String(128), nullable=False, index=True),
        sa.Column("old_value", JSON, nullable=True),
        sa.Column("new_value", JSON, nullable=True),
        sa.Column("changed_by", sa.String(256), nullable=True),
    )
    op.execute("ALTER TABLE dashboard_owner ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE config_audit ENABLE ROW LEVEL SECURITY")
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(STUBS)
    op.execute(FUNCTIONS)
    op.execute(
        """
DO $$ DECLARE t text; BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public'
           AND tablename <> 'alembic_version' LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS owner_read ON public.%I', t);
    EXECUTE format('CREATE POLICY owner_read ON public.%I FOR SELECT TO authenticated
                    USING (public.is_dashboard_owner())', t);
    EXECUTE format('GRANT SELECT ON public.%I TO authenticated', t);
  END LOOP;
END $$;
"""
    )
    op.execute(
        """
CREATE POLICY owner_switch_insert ON public.config FOR INSERT TO authenticated
  WITH CHECK (public.is_dashboard_owner() AND public.is_owner_switch(key)
              AND jsonb_typeof(value_json) = 'boolean');
CREATE POLICY owner_switch_update ON public.config FOR UPDATE TO authenticated
  USING (public.is_dashboard_owner() AND public.is_owner_switch(key))
  WITH CHECK (public.is_dashboard_owner() AND public.is_owner_switch(key)
              AND jsonb_typeof(value_json) = 'boolean');
GRANT INSERT, UPDATE ON public.config TO authenticated;
CREATE TRIGGER config_owner_switch_audit AFTER INSERT OR UPDATE OR DELETE ON public.config
  FOR EACH ROW EXECUTE FUNCTION public.log_owner_switch();
"""
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
DROP TRIGGER IF EXISTS config_owner_switch_audit ON public.config;
DROP POLICY IF EXISTS owner_switch_insert ON public.config;
DROP POLICY IF EXISTS owner_switch_update ON public.config;
REVOKE INSERT, UPDATE ON public.config FROM authenticated;
DO $$ DECLARE t text; BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
    EXECUTE format('DROP POLICY IF EXISTS owner_read ON public.%I', t);
    EXECUTE format('REVOKE SELECT ON public.%I FROM authenticated', t);
  END LOOP;
END $$;
DROP FUNCTION IF EXISTS public.log_owner_switch();
DROP FUNCTION IF EXISTS public.is_owner_switch(text);
DROP FUNCTION IF EXISTS public.is_dashboard_owner();
DROP FUNCTION IF EXISTS public.dashboard_email();
"""
        )
    op.drop_table("config_audit")
    op.drop_table("dashboard_owner")
