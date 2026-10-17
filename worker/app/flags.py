"""Feature flags (DESIGN_V2 section 0) and the engine version stamped on every signal.

A flag is the owner's switch (config table key, default on). `BUILT` says whether the code for
that stage exists yet. A stage runs only when both are true, and signals store exactly that, so
a flag can never claim a stage ran when it did not.
"""

from typing import Any

from sqlalchemy.orm import Session

from app.db.config_store import get_config

ENGINE_VERSION = "5.0.0"

DEFAULTS: dict[str, bool] = {
    "feat.event_study": True,
    "feat.implied_move_gate": True,
    "feat.probabilities": True,
    "feat.simulator": True,
    "feat.deep_data": True,
    "feat.knowledge_graph": True,
    "feat.multi_agent": True,
}

# Flip to True in the phase that ships each stage.
BUILT: dict[str, bool] = {
    "feat.event_study": True,  # Phase 4
    "feat.implied_move_gate": True,  # Phase 3a
    "feat.probabilities": True,  # Phase 5
    "feat.simulator": True,  # Phase 3a
    "feat.deep_data": False,  # Phase 7
    "feat.knowledge_graph": False,  # Phase 8
    "feat.multi_agent": True,  # Phase 5
}


def enabled(s: Session, name: str) -> bool:
    """Owner switch: config table value if set, else the default."""
    value = get_config(s, name)
    return DEFAULTS[name] if value is None else bool(value)


def active(s: Session, name: str) -> bool:
    return BUILT[name] and enabled(s, name)


def snapshot(s: Session) -> dict[str, Any]:
    """What is stored on each signal: switch state, build state, and whether it actually ran."""
    return {
        name: {"enabled": enabled(s, name), "built": BUILT[name], "active": active(s, name)}
        for name in DEFAULTS
    }
