"""phase 12 watchlist book (add-only columns)

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("signals", "paper_trades", "calibration_records"):
        op.add_column(
            table, sa.Column("book", sa.String(12), nullable=False, server_default="main")
        )
    op.create_index("ix_paper_trades_book", "paper_trades", ["book"])


def downgrade() -> None:
    op.drop_index("ix_paper_trades_book", "paper_trades")
    for table in ("signals", "paper_trades", "calibration_records"):
        op.drop_column(table, "book")
