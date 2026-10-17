import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base


@pytest.fixture(autouse=True)
def _history_store(tmp_path, monkeypatch):
    """Never touch a real history store from tests."""
    from app.config import get_settings

    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "history" / "h.duckdb"))
    monkeypatch.setenv("HISTORY_DATA_DIR", str(tmp_path / "history"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def engine() -> Engine:
    """In-memory SQLite with the full schema (one shared connection)."""
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def no_selection_gates(monkeypatch):
    """For tests about pricing/trading mechanics, not the owner's selection gates."""
    from app.signals import options_stage

    monkeypatch.setattr(options_stage, "selection_rejection", lambda r: None)
