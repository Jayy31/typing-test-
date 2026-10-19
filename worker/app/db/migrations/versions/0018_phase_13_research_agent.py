"""phase 13 research agent: research files, lessons, runs, tool-call log (add-only)

The dashboard may mark a lesson deleted (owner only, `deleted` false -> true, nothing else); a
trigger stamps who and when and writes the change to config_audit (key research.lesson.<id>).
Every new table gets the owner_read policy.

Revision ID: 0018
Revises: 0017
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
TS = sa.DateTime(timezone=True)
TABLES = ("research_runs", "research_tool_calls", "research_files", "research_lessons")


def upgrade() -> None:
    op.create_table(
        "research_runs",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(12), nullable=False, index=True),  # files|live|review|smoke
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("preset", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("effort", sa.String(12), nullable=True),
        sa.Column("symbol", sa.String(32), nullable=True, index=True),
        sa.Column("subject", sa.String(96), nullable=True),
        sa.Column("analysis_id", BIGID, sa.ForeignKey("analyses.id"), nullable=True),
        sa.Column("research_file_id", BIGID, nullable=True),
        sa.Column("status", sa.String(12), nullable=False, index=True),  # ok|limited|failed|...
        sa.Column("limited_reason", sa.String(64), nullable=True),
        sa.Column("batch_id", sa.String(96), nullable=True),
        sa.Column("tool_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("web_searches", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("web_fetches", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_inr", sa.Float(), nullable=False, server_default="0"),
        sa.Column("estimate_inr", sa.Float(), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("bear_case", sa.Text(), nullable=True),
        sa.Column("sources_json", JSON, nullable=True),
        sa.Column("lessons_json", JSON, nullable=True),
        sa.Column("output_json", JSON, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now(), index=True),
    )
    op.create_table(
        "research_tool_calls",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column("run_id", BIGID, sa.ForeignKey("research_runs.id"), nullable=False, index=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("tool", sa.String(48), nullable=False),
        sa.Column("args_json", JSON, nullable=True),
        sa.Column("rows", sa.Integer(), nullable=True),
        sa.Column("is_error", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "research_files",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column("subject_kind", sa.String(8), nullable=False),  # stock | theme
        sa.Column("subject", sa.String(96), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_json", JSON, nullable=False),
        sa.Column("sources_json", JSON, nullable=True),
        sa.Column("provider", sa.String(16), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("preset", sa.String(16), nullable=True),
        sa.Column("run_id", BIGID, sa.ForeignKey("research_runs.id"), nullable=True),
        sa.Column("cost_inr", sa.Float(), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("subject_kind", "subject", "version", name="uq_research_files"),
    )
    op.create_table(
        "research_lessons",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column("scope_kind", sa.String(12), nullable=False),  # stock | sector | event_type
        sa.Column("scope", sa.String(96), nullable=False, index=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("evidence_json", JSON, nullable=True),
        sa.Column("run_id", BIGID, sa.ForeignKey("research_runs.id"), nullable=True),
        sa.Column("week", sa.Date(), nullable=True),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", TS, nullable=True),
        sa.Column("deleted_by", sa.String(256), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    )
    # prediction tags (source agent|panel|single already exists): preset, models per role, cost
    op.add_column("analyses", sa.Column("research_run_id", BIGID, nullable=True))
    op.add_column("analyses", sa.Column("ai_preset", sa.String(16), nullable=True))
    op.add_column("analyses", sa.Column("ai_models_json", JSON, nullable=True))
    op.add_column("analyses", sa.Column("ai_cost_inr", sa.Float(), nullable=True))
    op.add_column("signals", sa.Column("ai_preset", sa.String(16), nullable=True))
    op.add_column("prediction_scores", sa.Column("ai_preset", sa.String(16), nullable=True))
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
CREATE POLICY owner_delete_lesson ON public.research_lessons FOR UPDATE TO authenticated
  USING (public.is_dashboard_owner() AND deleted = false)
  WITH CHECK (public.is_dashboard_owner() AND deleted = true);
GRANT UPDATE (deleted) ON public.research_lessons TO authenticated;

CREATE OR REPLACE FUNCTION public.stamp_lesson_delete() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $f$
BEGIN
  IF NEW.deleted AND NOT OLD.deleted THEN
    NEW.deleted_at := now();
    NEW.deleted_by := coalesce(public.dashboard_email(), current_user);
    INSERT INTO public.config_audit (key, old_value, new_value, changed_by)
    VALUES ('research.lesson.' || OLD.id,
            jsonb_build_object('scope', OLD.scope_kind || ':' || OLD.scope, 'text', OLD.text),
            to_jsonb('deleted'::text), NEW.deleted_by);
  END IF;
  RETURN NEW;
END $f$;
CREATE TRIGGER lesson_delete BEFORE UPDATE ON public.research_lessons
  FOR EACH ROW EXECUTE FUNCTION public.stamp_lesson_delete();
"""
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS lesson_delete ON public.research_lessons")
        op.execute("DROP FUNCTION IF EXISTS public.stamp_lesson_delete()")
    op.drop_column("prediction_scores", "ai_preset")
    op.drop_column("signals", "ai_preset")
    for c in ("ai_cost_inr", "ai_models_json", "ai_preset", "research_run_id"):
        op.drop_column("analyses", c)
    for t in ("research_lessons", "research_files", "research_tool_calls", "research_runs"):
        op.drop_table(t)
