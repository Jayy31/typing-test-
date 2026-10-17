"""phase 5: multi-agent panel, calibration, post-mortems, digests

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.add_column("analyses", sa.Column("prediction_source", sa.String(length=8), nullable=True))
    op.add_column("signals", sa.Column("prediction_source", sa.String(length=8), nullable=True))
    op.add_column("signals", sa.Column("calibration_method", sa.String(length=16), nullable=True))
    op.add_column("signals", sa.Column("ai_weight", sa.Float(), nullable=True))
    op.add_column(
        "prediction_scores", sa.Column("prediction_source", sa.String(length=8), nullable=True)
    )
    op.add_column(
        "calibrators",
        sa.Column("source", sa.String(length=8), server_default="all", nullable=False),
    )
    op.add_column("calibrators", sa.Column("n_events", sa.Integer(), nullable=True))
    op.add_column("calibration_records", sa.Column("analysis_id", BIGID, nullable=True))
    op.add_column("calibration_records", sa.Column("rejection_id", BIGID, nullable=True))
    op.add_column("calibration_records", sa.Column("symbol", sa.String(length=32), nullable=True))
    op.add_column("calibration_records", sa.Column("source", sa.String(length=8), nullable=True))
    op.add_column(
        "calibration_records", sa.Column("cluster_key", sa.String(length=64), nullable=True)
    )
    op.add_column("calibration_records", sa.Column("predicted_used", sa.Float(), nullable=True))
    op.create_foreign_key(
        "fk_calibration_records_analysis_id",
        "calibration_records",
        "analyses",
        ["analysis_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_calibration_records_rejection_id",
        "calibration_records",
        "rejected_candidates",
        ["rejection_id"],
        ["id"],
    )
    op.create_index("ix_calibration_records_analysis_id", "calibration_records", ["analysis_id"])
    op.create_index("ix_calibration_records_cluster_key", "calibration_records", ["cluster_key"])
    op.create_table(
        "post_mortems",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column("trade_id", BIGID, sa.ForeignKey("paper_trades.id"), nullable=False),
        sa.Column("analysis_id", BIGID, sa.ForeignKey("analyses.id"), nullable=True),
        sa.Column("prediction_source", sa.String(length=8), nullable=True),
        sa.Column("primary_cause", sa.String(length=24), nullable=False),
        sa.Column("secondary_causes", JSON, nullable=True),
        sa.Column("thesis_correct", sa.Boolean(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("output_json", JSON, nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_ver", sa.String(length=32), nullable=False),
        sa.Column("cost_inr", sa.Float(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("trade_id", name="uq_post_mortems_trade_id"),
    )
    op.create_index("ix_post_mortems_primary_cause", "post_mortems", ["primary_cause"])
    op.create_table(
        "digests",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("data_json", JSON, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("week_start", name="uq_digests_week_start"),
    )
    op.execute("ALTER TABLE post_mortems ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE digests ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("digests")
    op.drop_index("ix_post_mortems_primary_cause", table_name="post_mortems")
    op.drop_table("post_mortems")
    op.drop_index("ix_calibration_records_cluster_key", table_name="calibration_records")
    op.drop_index("ix_calibration_records_analysis_id", table_name="calibration_records")
    op.drop_constraint(
        "fk_calibration_records_rejection_id", "calibration_records", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_calibration_records_analysis_id", "calibration_records", type_="foreignkey"
    )
    for col in ("predicted_used", "cluster_key", "source", "symbol", "rejection_id", "analysis_id"):
        op.drop_column("calibration_records", col)
    op.drop_column("calibrators", "n_events")
    op.drop_column("calibrators", "source")
    op.drop_column("prediction_scores", "prediction_source")
    op.drop_column("signals", "ai_weight")
    op.drop_column("signals", "calibration_method")
    op.drop_column("signals", "prediction_source")
    op.drop_column("analyses", "prediction_source")
