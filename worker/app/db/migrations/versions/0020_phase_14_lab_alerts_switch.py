"""phase 14: owner switch for strategy signal alerts (lab_books.alerts_enabled), logged

The dashboard may change only `alerts_enabled` on a strategy book (owner only); a trigger logs
every change to config_audit (key lab.alerts.<book>). Add-only.

Revision ID: 0020
Revises: 0019
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
CREATE POLICY owner_alerts_switch ON public.lab_books FOR UPDATE TO authenticated
  USING (public.is_dashboard_owner()) WITH CHECK (public.is_dashboard_owner());
GRANT UPDATE (alerts_enabled) ON public.lab_books TO authenticated;

CREATE OR REPLACE FUNCTION public.log_lab_alerts_switch() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $f$
BEGIN
  IF NEW.alerts_enabled IS DISTINCT FROM OLD.alerts_enabled THEN
    INSERT INTO public.config_audit (key, old_value, new_value, changed_by)
    VALUES ('lab.alerts.' || OLD.book, to_jsonb(OLD.alerts_enabled), to_jsonb(NEW.alerts_enabled),
            coalesce(public.dashboard_email(), current_user));
  END IF;
  RETURN NEW;
END $f$;
CREATE TRIGGER lab_alerts_switch AFTER UPDATE ON public.lab_books
  FOR EACH ROW EXECUTE FUNCTION public.log_lab_alerts_switch();
"""
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER IF EXISTS lab_alerts_switch ON public.lab_books")
    op.execute("DROP FUNCTION IF EXISTS public.log_lab_alerts_switch()")
    op.execute("DROP POLICY IF EXISTS owner_alerts_switch ON public.lab_books")
