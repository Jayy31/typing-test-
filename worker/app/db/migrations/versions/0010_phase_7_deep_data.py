"""phase 7 deep data sources

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
TS = sa.DateTime(timezone=True)

TABLES = [
    "insider_trades",
    "bulk_block_deals",
    "shareholding",
    "ratings",
    "regulatory_actions",
    "results_extracts",
    "business_updates",
    "macro_releases",
    "stock_stats",
]


def _structured() -> list[sa.Column]:
    return [
        sa.Column("subtype", sa.String(64), nullable=True),
        sa.Column("material", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("materiality_json", JSON, nullable=True),
        sa.Column("news_id", BIGID, sa.ForeignKey("news_items.id"), nullable=True),
        sa.Column("raw_json", JSON, nullable=True),
    ]


def _id() -> sa.Column:
    return sa.Column("id", BIGID, primary_key=True, autoincrement=True)


def upgrade() -> None:
    op.add_column("signals", sa.Column("enter_after", TS, nullable=True))
    op.add_column("macro_snapshots", sa.Column("commodities_json", JSON, nullable=True))
    op.create_table(
        "insider_trades",
        _id(),
        sa.Column("ref", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False, index=True),
        sa.Column("person", sa.Text(), nullable=True),
        sa.Column("person_category", sa.String(64), nullable=True),
        sa.Column("counterparty", sa.String(16), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("value_inr", sa.Float(), nullable=True),
        sa.Column("mode", sa.String(64), nullable=True),
        sa.Column("event_date", sa.Date(), nullable=True),
        sa.Column("disclosed_at", TS, nullable=False, index=True),
        *_structured(),
        sa.UniqueConstraint("ref", name="uq_insider_trades_ref"),
    )
    op.create_table(
        "bulk_block_deals",
        _id(),
        sa.Column("ref", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False, index=True),
        sa.Column("client", sa.Text(), nullable=True),
        sa.Column("counterparty", sa.String(16), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("value_inr", sa.Float(), nullable=True),
        sa.Column("event_date", sa.Date(), nullable=True),
        sa.Column("disclosed_at", TS, nullable=False, index=True),
        *_structured(),
        sa.UniqueConstraint("ref", name="uq_bulk_block_deals_ref"),
    )
    op.create_table(
        "shareholding",
        _id(),
        sa.Column("symbol", sa.String(32), nullable=False, index=True),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("disclosed_at", TS, nullable=False, index=True),
        sa.Column("promoter_pct", sa.Float(), nullable=True),
        sa.Column("fii_pct", sa.Float(), nullable=True),
        sa.Column("dii_pct", sa.Float(), nullable=True),
        sa.Column("public_pct", sa.Float(), nullable=True),
        sa.Column("pledge_pct", sa.Float(), nullable=True),
        sa.Column("subtypes", JSON, nullable=True),
        *_structured(),
        sa.UniqueConstraint("symbol", "event_date", name="uq_shareholding"),
    )
    op.create_table(
        "ratings",
        _id(),
        sa.Column("ref", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False, index=True),
        sa.Column("agency", sa.String(32), nullable=True),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=True),
        sa.Column("disclosed_at", TS, nullable=False, index=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        *_structured(),
        sa.UniqueConstraint("ref", name="uq_ratings_ref"),
    )
    op.create_table(
        "regulatory_actions",
        _id(),
        sa.Column("ref", sa.String(64), nullable=False),
        sa.Column("regulator", sa.String(16), nullable=False),
        sa.Column("action_type", sa.String(64), nullable=True),
        sa.Column("symbol", sa.String(32), nullable=True, index=True),
        sa.Column("event_date", sa.Date(), nullable=True),
        sa.Column("disclosed_at", TS, nullable=False, index=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        *_structured(),
        sa.UniqueConstraint("ref", name="uq_regulatory_actions_ref"),
    )
    op.create_table(
        "results_extracts",
        _id(),
        sa.Column("symbol", sa.String(32), nullable=False, index=True),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("disclosed_at", TS, nullable=False, index=True),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("consolidated", sa.Boolean(), nullable=True),
        sa.Column("revenue", sa.Float(), nullable=True),
        sa.Column("pat", sa.Float(), nullable=True),
        sa.Column("ebitda", sa.Float(), nullable=True),
        sa.Column("revenue_yoy_pct", sa.Float(), nullable=True),
        sa.Column("pat_yoy_pct", sa.Float(), nullable=True),
        sa.Column("margin_change_pp", sa.Float(), nullable=True),
        sa.Column("guidance", sa.String(16), nullable=True),
        sa.Column("surprise_score", sa.Float(), nullable=True),
        sa.Column("surprise_json", JSON, nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("prompt_ver", sa.String(32), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("latency_s", sa.Float(), nullable=True),
        *_structured(),
        sa.UniqueConstraint("symbol", "period_end", name="uq_results_extracts"),
    )
    op.create_table(
        "business_updates",
        _id(),
        sa.Column("symbol", sa.String(32), nullable=False, index=True),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("metric", sa.String(32), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("yoy_pct", sa.Float(), nullable=True),
        sa.Column("disclosed_at", TS, nullable=False, index=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("prompt_ver", sa.String(32), nullable=True),
        *_structured(),
        sa.UniqueConstraint("symbol", "period", "metric", name="uq_business_updates"),
    )
    op.create_table(
        "macro_releases",
        _id(),
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("prior", sa.Float(), nullable=True),
        sa.Column("released_at", TS, nullable=False),
        sa.Column("source", sa.String(32), nullable=True),
        sa.UniqueConstraint("name", "period", name="uq_macro_releases"),
    )
    op.create_table(
        "stock_stats",
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("adv20_inr", sa.Float(), nullable=True),
        sa.Column("issued_shares", sa.Float(), nullable=True),
        sa.Column("close", sa.Float(), nullable=True),
        sa.Column("mcap_inr", sa.Float(), nullable=True),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )
    for t in TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    for t in reversed(TABLES):
        op.drop_table(t)
    op.drop_column("macro_snapshots", "commodities_json")
    op.drop_column("signals", "enter_after")
