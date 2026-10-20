"""phase 14 strategy lab: hypotheses, specs, trials, verdicts, strategy books (add-only)

Pre-registration: a hypothesis's rationale and expected effect cannot change after it is
registered (trigger on Postgres; the app never updates them). A holdout verdict exists at most
once per spec (unique index). Every new table gets RLS and the owner_read policy.

Revision ID: 0019
Revises: 0018
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
TS = sa.DateTime(timezone=True)
TABLES = ("lab_hypotheses", "lab_specs", "lab_trials", "lab_verdicts", "lab_books")


def upgrade() -> None:
    op.create_table(
        "lab_hypotheses",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("source", sa.String(20), nullable=False, index=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("expected_effect", sa.Text(), nullable=False),
        sa.Column("expected_json", JSON, nullable=True),
        sa.Column("ai_memory_risk", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(20), nullable=False, index=True),
        sa.Column("origin_json", JSON, nullable=True),  # model / detector / pattern id
        sa.Column("registered_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=True),
    )
    op.create_table(
        "lab_specs",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column(
            "hypothesis_id", BIGID, sa.ForeignKey("lab_hypotheses.id"), nullable=False, index=True
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("spec_yaml", sa.Text(), nullable=False),
        sa.Column("spec_hash", sa.String(16), nullable=False, unique=True),
        sa.Column("parent_spec_id", BIGID, nullable=True),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("stage", sa.String(20), nullable=False, server_default="stage0"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("hypothesis_id", "version", name="uq_lab_specs_version"),
    )
    op.create_table(
        "lab_trials",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column("spec_id", BIGID, sa.ForeignKey("lab_specs.id"), nullable=False, index=True),
        sa.Column(
            "kind", sa.String(20), nullable=False
        ),  # stage0|plateau|placebo|cost|holdout|eod|real
        sa.Column("label", sa.String(64), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("n_trades", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_events", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expectancy_pct", sa.Float(), nullable=True),
        sa.Column("ci_lo", sa.Float(), nullable=True),
        sa.Column("ci_hi", sa.Float(), nullable=True),
        sa.Column("win_rate", sa.Float(), nullable=True),
        sa.Column("sharpe", sa.Float(), nullable=True),
        sa.Column("metrics_json", JSON, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "lab_verdicts",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column("spec_id", BIGID, sa.ForeignKey("lab_specs.id"), nullable=False, index=True),
        sa.Column(
            "stage", sa.String(20), nullable=False
        ),  # stage0|holdout|incubation|validated|retired
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("checks_json", JSON, nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "uq_lab_holdout_once",
        "lab_verdicts",
        ["spec_id"],
        unique=True,
        postgresql_where=sa.text("stage = 'holdout'"),
        sqlite_where=sa.text("stage = 'holdout'"),
    )
    op.create_table(
        "lab_books",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column("spec_id", BIGID, sa.ForeignKey("lab_specs.id"), nullable=False, unique=True),
        sa.Column("book", sa.String(12), nullable=False, unique=True),  # paper_trades.book value
        sa.Column(
            "status", sa.String(16), nullable=False, index=True
        ),  # incubating|validated|retired
        sa.Column("alerts_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("started_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("validated_at", TS, nullable=True),
        sa.Column("retired_at", TS, nullable=True),
        sa.Column("retire_reason", sa.Text(), nullable=True),
        sa.Column("stats_json", JSON, nullable=True),
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
CREATE OR REPLACE FUNCTION public.lab_preregistration_lock() RETURNS trigger
  LANGUAGE plpgsql AS $f$
BEGIN
  IF NEW.rationale IS DISTINCT FROM OLD.rationale
     OR NEW.expected_effect IS DISTINCT FROM OLD.expected_effect
     OR NEW.expected_json IS DISTINCT FROM OLD.expected_json
     OR NEW.source IS DISTINCT FROM OLD.source THEN
    RAISE EXCEPTION 'hypothesis % is pre-registered: rationale is frozen', OLD.id;
  END IF;
  RETURN NEW;
END $f$;
CREATE TRIGGER lab_preregistration BEFORE UPDATE ON public.lab_hypotheses
  FOR EACH ROW EXECUTE FUNCTION public.lab_preregistration_lock();
"""
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS lab_preregistration ON public.lab_hypotheses")
        op.execute("DROP FUNCTION IF EXISTS public.lab_preregistration_lock()")
    op.drop_index("uq_lab_holdout_once", "lab_verdicts")
    for t in reversed(TABLES):
        op.drop_table(t)
