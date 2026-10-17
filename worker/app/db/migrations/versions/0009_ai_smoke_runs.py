"""ai smoke runs

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "ai_smoke_runs",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("total_cost_inr", sa.Float(), nullable=False),
        sa.Column("panel_seconds", sa.Float(), nullable=True),
        sa.Column("results_json", JSON, nullable=False),
        sa.Column("fixes_json", JSON, nullable=True),
    )
    op.execute("ALTER TABLE ai_smoke_runs ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("ai_smoke_runs")
