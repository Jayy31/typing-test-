"""Resolved model per AI role (AI_MODEL_* plus the active research preset) and the
AI_ALLOWED_MODELS check run by preflight and at worker startup."""

from sqlalchemy.engine import Engine

from app.config import Settings, get_settings
from app.db.session import session_scope

ENV_ROLES = (
    "tagger",
    "analyst",
    "researcher",
    "novelty",
    "bull",
    "bear",
    "judge",
    "postmortem",
    "results_fallback",
)


def allowed(settings: Settings | None = None) -> set[str]:
    st = settings or get_settings()
    return {m.strip() for m in st.ai_allowed_models.split(",") if m.strip()}


def resolved(settings: Settings | None = None) -> dict[str, str]:
    """`AI_MODEL_<ROLE>` per env role, then each research role of the active preset."""
    from app.research import config as rcfg

    st = settings or get_settings()
    out = {f"AI_MODEL_{r.upper()}": str(getattr(st, f"ai_model_{r}")) for r in ENV_ROLES}
    preset = rcfg.preset_name()
    for role in rcfg.ROLES:
        try:
            out[f"{role} ({preset})"] = rcfg.role_config(role, preset).model
        except Exception as exc:  # noqa: BLE001 - reported as a problem, never crashes startup
            out[f"{role} ({preset})"] = f"<error: {exc}>"
    return out


def problems(settings: Settings | None = None) -> list[str]:
    ok = allowed(settings)
    return [
        f"{role} = {model or '<blank>'} is not in AI_ALLOWED_MODELS"
        for role, model in resolved(settings).items()
        if model not in ok
    ]


def startup_check(engine: Engine | None = None) -> int:
    """Health alert (Telegram) naming every role whose model is not allowed, then raise so the
    `ai_models_check` job run is marked failed. Returns 0 when every model is allowed."""
    from app.notify.outbox import enqueue_alert

    found = problems()
    if found:
        text = (
            "AI model check failed: these roles resolve to models outside AI_ALLOWED_MODELS ("
            + ", ".join(sorted(allowed()))
            + "):\n- "
            + "\n- ".join(found)
            + "\nFix the AI_MODEL_* / RESEARCH_* values in the VM .env, or add the model to "
            "AI_ALLOWED_MODELS."
        )
        with session_scope(engine) as s:
            enqueue_alert(s, "health", text, "ai_models:" + "|".join(found))
        raise RuntimeError(f"{len(found)} AI role(s) use a model outside AI_ALLOWED_MODELS")
    return 0
