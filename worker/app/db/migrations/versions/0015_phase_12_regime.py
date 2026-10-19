"""phase 12 market regime (add-only)

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_regime",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("vol", sa.String(12), nullable=False),
        sa.Column("direction", sa.String(12), nullable=False),
        sa.Column("label", sa.String(32), nullable=False),
        sa.Column("inputs", sa.String(32), nullable=False),
        sa.Column("vix", sa.Float()),
        sa.Column("vix_pct", sa.Float()),
        sa.Column("nifty_ret_pct", sa.Float()),
        sa.Column("fii_net_cr", sa.Float()),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.execute("ALTER TABLE market_regime ENABLE ROW LEVEL SECURITY")
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE POLICY owner_read ON public.market_regime FOR SELECT TO authenticated "
            "USING (public.is_dashboard_owner())"
        )
        op.execute("GRANT SELECT ON public.market_regime TO authenticated")


def downgrade() -> None:
    op.drop_table("market_regime")
