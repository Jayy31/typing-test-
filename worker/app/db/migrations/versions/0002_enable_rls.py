"""enable row-level security on all tables

Supabase exposes the public schema over its REST API, so without RLS the anon key could
read every table. With RLS on and no policies, anon/authenticated roles see nothing. The
worker connects with the table-owner role (DATABASE_URL), which bypasses RLS. Read-only
policies for the dashboard are added in Phase 4.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TABLES = [
    "ai_usage",
    "analyses",
    "config",
    "events_calendar",
    "instruments",
    "job_runs",
    "macro_snapshots",
    "news_clusters",
    "news_items",
    "news_tags",
    "option_quotes",
    "paper_trades",
    "rules",
    "signal_versions",
    "signals",
    "trade_events",
]


def upgrade() -> None:
    for t in TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    for t in TABLES:
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
