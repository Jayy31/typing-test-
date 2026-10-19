"""phase 12 positioning snapshots (add-only)

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "positioning_snapshots",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("states_json", JSON, nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.execute("ALTER TABLE positioning_snapshots ENABLE ROW LEVEL SECURITY")
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE POLICY owner_read ON public.positioning_snapshots FOR SELECT TO authenticated "
            "USING (public.is_dashboard_owner())"
        )
        op.execute("GRANT SELECT ON public.positioning_snapshots TO authenticated")


def downgrade() -> None:
    op.drop_table("positioning_snapshots")
