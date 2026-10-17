"""phase 10 analytics: monthly reports, learning-loop suggestions, AI vs rules, alert files

The dashboard may change a suggestion's status from pending to accepted / rejected (owner only,
nothing else in the row); a trigger stamps who and when. Every new table gets the owner_read policy.

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
TS = sa.DateTime(timezone=True)
TABLES = ("monthly_reports", "suggestions", "ai_vs_rules")


def upgrade() -> None:
    op.add_column("alerts", sa.Column("file_path", sa.Text(), nullable=True))
    op.create_table(
        "monthly_reports",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column("month", sa.Date(), nullable=False, unique=True),
        sa.Column("data_json", JSON, nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("ai_summary", sa.Text(), nullable=True),
        sa.Column("ai_summary_status", sa.String(24), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "suggestions",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column("month", sa.Date(), nullable=False, index=True),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("param", sa.String(64), nullable=False),
        sa.Column("file", sa.String(128), nullable=False),
        sa.Column("yaml_path", sa.String(128), nullable=False),
        sa.Column("current_value", sa.Float(), nullable=False),
        sa.Column("proposed_value", sa.Float(), nullable=False),
        sa.Column("expected_effect", sa.Float(), nullable=False),
        sa.Column("evidence_json", JSON, nullable=False),
        sa.Column("status", sa.String(12), nullable=False, index=True),
        sa.Column("decided_at", TS, nullable=True),
        sa.Column("decided_by", sa.String(256), nullable=True),
        sa.Column("pr_url", sa.Text(), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("applied_at", TS, nullable=True),
        sa.Column("review_json", JSON, nullable=True),
        sa.Column("reverts_id", BIGID, sa.ForeignKey("suggestions.id"), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("month", "param", "kind", name="uq_suggestions"),
    )
    op.create_table(
        "ai_vs_rules",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column("analysis_id", BIGID, sa.ForeignKey("analyses.id"), nullable=False, unique=True),
        sa.Column("symbol", sa.String(32), nullable=False, index=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("news_direction", sa.String(8), nullable=False),
        sa.Column("ai_direction", sa.String(8), nullable=True),
        sa.Column("ai_signal", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("rules_direction", sa.String(8), nullable=True),
        sa.Column("rules_reason", sa.Text(), nullable=True),
        sa.Column("analogue_n", sa.Integer(), nullable=True),
        sa.Column("analogue_mean", sa.Float(), nullable=True),
        sa.Column("realised_move_pct", sa.Float(), nullable=True),
        sa.Column("resolved_at", TS, nullable=True),
    )
    for t in TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
    if op.get_bind().dialect.name != "postgresql":
        return
    for t in TABLES:
        op.execute(
            f"CREATE POLICY owner_read ON public.{t} FOR SELECT TO authenticated "
            "USING (public.is_dashboard_owner())"
        )
        op.execute(f"GRANT SELECT ON public.{t} TO authenticated")
    op.execute(
        """
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
  FOR EACH ROW EXECUTE FUNCTION public.stamp_suggestion_decision();
"""
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS suggestion_decision ON public.suggestions")
        op.execute("DROP FUNCTION IF EXISTS public.stamp_suggestion_decision()")
    for t in reversed(TABLES):
        op.drop_table(t)
    op.drop_column("alerts", "file_path")
