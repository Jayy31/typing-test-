"""Migration chain stays linear and covers every model table (no DB needed)."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db.models import Base

ROOT = Path(__file__).resolve().parents[1]


def test_single_head_and_rls_covers_all_tables() -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "app/db/migrations"))
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1

    rls = (ROOT / "app/db/migrations/versions/0002_enable_rls.py").read_text()
    missing = [t for t in Base.metadata.tables if f'"{t}"' not in rls]
    assert not missing, f"tables without RLS in migration 0002: {missing}"
