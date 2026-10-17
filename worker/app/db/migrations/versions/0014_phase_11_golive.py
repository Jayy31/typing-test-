"""phase 11 go-live checks (add-only)

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "golive_checks",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("results_json", JSON, nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
    )
    op.execute("ALTER TABLE golive_checks ENABLE ROW LEVEL SECURITY")
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE POLICY owner_read ON public.golive_checks FOR SELECT TO authenticated "
            "USING (public.is_dashboard_owner())"
        )
        op.execute("GRANT SELECT ON public.golive_checks TO authenticated")


def downgrade() -> None:
    op.drop_table("golive_checks")
