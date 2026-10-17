"""phase 8 knowledge graph

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("kg_edges", sa.Column("exposure_kind", sa.String(24), nullable=True))
    op.add_column("kg_edges", sa.Column("exposure_pct", sa.Float(), nullable=True))
    op.add_column("kg_edges", sa.Column("valid_from", sa.Date(), nullable=True))
    op.add_column("kg_edges", sa.Column("valid_to", sa.Date(), nullable=True))
    op.add_column("kg_edges", sa.Column("origin", sa.String(16), nullable=True))
    op.add_column(
        "kg_edges",
        sa.Column("evidence_news_id", BIGID, sa.ForeignKey("news_items.id"), nullable=True),
    )
    op.add_column("analyses", sa.Column("impact_order", sa.Integer(), nullable=True))
    op.add_column("regulatory_actions", sa.Column("match_confidence", sa.Float(), nullable=True))
    op.add_column("regulatory_actions", sa.Column("match_candidates", JSON, nullable=True))
    op.create_table(
        "kg_paths",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column(
            "source_news_id", BIGID, sa.ForeignKey("news_items.id"), nullable=False, index=True
        ),
        sa.Column("source_symbol", sa.String(32), nullable=False),
        sa.Column("target", sa.String(32), nullable=False, index=True),
        sa.Column("path_key", sa.String(256), nullable=False),
        sa.Column("hops", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(12), nullable=False),
        sa.Column("sign_basis", sa.String(12), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("sentiment", sa.Float(), nullable=True),
        sa.Column("news_id", BIGID, sa.ForeignKey("news_items.id"), nullable=True),
        sa.Column("detail_json", JSON, nullable=True),
        sa.UniqueConstraint("source_news_id", "target", "path_key", name="uq_kg_paths"),
    )
    op.execute("ALTER TABLE kg_paths ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("kg_paths")
    for col in ("match_candidates", "match_confidence"):
        op.drop_column("regulatory_actions", col)
    op.drop_column("analyses", "impact_order")
    for col in (
        "evidence_news_id",
        "origin",
        "valid_to",
        "valid_from",
        "exposure_pct",
        "exposure_kind",
    ):
        op.drop_column("kg_edges", col)
