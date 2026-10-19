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

    monkeypatch.setattr(options_stage, "selection_rejection", lambda r, gates=None: None)


BLANK_AI_DEFAULTS = (
    "ai_model_tagger",
    "ai_model_analyst",
    "ai_model_researcher",
    "ai_model_novelty",
    "ai_model_bull",
    "ai_model_bear",
    "ai_model_judge",
    "ai_model_postmortem",
    "ai_model_results_fallback",
    "ai_model_knowledge_cutoff",
)


@pytest.fixture(autouse=True)
def _no_default_ai_models(request):
    """Tests configure the AI roles (and the R1 cutoff) they use; the shipped defaults in
    app.config are checked by tests marked `real_ai_defaults`."""
    if request.node.get_closest_marker("real_ai_defaults"):
        yield
        return
    from app.config import Settings

    saved = {f: Settings.model_fields[f].default for f in BLANK_AI_DEFAULTS}
    for f in BLANK_AI_DEFAULTS:
        Settings.model_fields[f].default = ""
    Settings.model_rebuild(force=True)
    yield
    for f, v in saved.items():
        Settings.model_fields[f].default = v
    Settings.model_rebuild(force=True)


@pytest.fixture(autouse=True)
def _market_hours(monkeypatch):
    """Signal cycles run on the wall clock: treat tests as within market hours unless a test
    opts into after-hours behaviour (tests/test_deep_data.py)."""
    from app.signals import deferred

    monkeypatch.setattr(deferred, "off_hours", lambda now, holidays: False)


@pytest.fixture(autouse=True, scope="session")
def _core_rules_everywhere():
    """CORE RULES: every signal, paper trade and backtest trade any test stores must pass the
    core-rules guard (stock only, single long leg, CE for bullish / PE for bearish, monthly
    expiry no later than next month). A violation fails the test that wrote it."""
    from datetime import datetime

    from sqlalchemy import event

    from app.db.models import BacktestTrade, PaperTrade, Signal
    from app.trading.core_rules import violation

    def day(x):
        return (x.date() if isinstance(x, datetime) else x) or datetime.now().date()

    def check(kind, symbol, direction, opt, expiry, when):
        bad = violation(symbol, direction, opt, expiry, day(when))
        assert bad is None, f"core rule broken by a stored {kind}: {bad}"

    def sig(_m, _c, t):
        if t.option_type and (t.expiry or t.pricing_status == "priced"):  # priced: has a contract
            check("signal", t.symbol, t.direction, t.option_type, t.expiry, t.created_at)

    def paper(_m, _c, t):
        direction = "bullish" if t.option_type == "CE" else "bearish"
        if t.symbol and t.option_type:
            check("paper trade", t.symbol, direction, t.option_type, t.expiry, t.entry_ts)

    def bt(_m, _c, t):
        opt = (t.details_json or {}).get("option_type")
        check("backtest trade", t.symbol, t.direction, opt, t.expiry, t.entry_date)

    listeners = [(Signal, sig), (PaperTrade, paper), (BacktestTrade, bt)]
    for model, fn in listeners:
        event.listen(model, "before_insert", fn)
    yield
    for model, fn in listeners:
        event.remove(model, "before_insert", fn)
