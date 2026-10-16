import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base


@pytest.fixture
def engine() -> Engine:
    """In-memory SQLite with the full schema (one shared connection)."""
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(eng)
    return eng
