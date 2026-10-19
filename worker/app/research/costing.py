"""Rupee cost of research-agent requests: tokens (ai.yaml per model, incl. Haiku's long-prompt
rate) plus server-tool fees (web search per 1,000)."""

from app.ai.client import compute_cost_inr
from app.ai.providers.base import Usage
from app.yaml_config import load_yaml


def cost_inr(role: str, model: str, u: Usage, batch: bool = False) -> float:
    """Cost of ONE request (or several billed at the same tier) in INR."""
    tokens = compute_cost_inr(
        role, u.input_tokens, u.output_tokens, u.cache_read, u.cache_write, batch, model
    )
    cfg = load_yaml("ai.yaml")
    st = cfg.get("server_tools") or {}
    usd = (
        u.web_searches * float(st.get("web_search_per_1000_usd", 10.0))
        + u.web_fetches * float(st.get("web_fetch_per_1000_usd", 0.0))
    ) / 1000
    return round(tokens + usd * float(cfg["usd_inr"]), 4)


def long_context_threshold(model: str) -> int | None:
    m = (load_yaml("ai.yaml").get("models") or {}).get(model) or {}
    lc = m.get("long_context")
    return int(lc["threshold"]) if lc else None
