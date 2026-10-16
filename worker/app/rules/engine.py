"""Rules engine: sector linkage map (macro moves) and event playbooks (news evidence).

Rules never create signals on their own. They add or subtract points on top of the AI
judgement; both numbers are stored separately for later comparison.
"""

from dataclasses import dataclass, field
from typing import Any

from app.yaml_config import load_yaml


@dataclass(frozen=True)
class RuleContext:
    """What the engine knows about one candidate stock."""

    symbol: str
    sector: str
    sub_sector: str | None
    text: str  # headlines + summaries of the evidence
    themes: frozenset[str] = field(default_factory=frozenset)
    event_types: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class RuleHit:
    name: str
    kind: str  # "macro" | "playbook"
    direction: str  # "bullish" | "bearish"
    weight: float
    why: str


def fired_macro_rules(changes: dict[str, float]) -> list[dict[str, Any]]:
    """Macro rules whose threshold is met. `changes` maps metric -> % change (or abs for us10y)."""
    fired: list[dict[str, Any]] = []
    for rule in load_yaml("linkage_map.yaml")["macro_moves"]:
        change = changes.get(rule["metric"])
        if change is None:
            continue
        move = change if rule["move"] == "up" else -change
        if move >= float(rule["threshold"]):
            fired.append({**rule, "observed": change})
    return fired


def _targets(effect: dict[str, Any], ctx: RuleContext) -> bool:
    return ctx.sector in effect.get("sectors", []) or (
        ctx.sub_sector is not None and ctx.sub_sector in effect.get("sub_sectors", [])
    )


def evaluate(ctx: RuleContext, macro_fired: list[dict[str, Any]]) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for rule in macro_fired:
        for eff in rule["effects"]:
            if _targets(eff, ctx):
                why = f"{rule['metric']} moved {rule['observed']:+.2f} ({rule['mode']})"
                hits.append(
                    RuleHit(rule["name"], "macro", eff["direction"], float(eff["weight"]), why)
                )
    text = ctx.text.lower()
    for pb in load_yaml("playbooks.yaml")["playbooks"]:
        phrase = next((k for k in pb["keywords_any"] if k.lower() in text), None)
        if phrase is None:
            continue
        if pb.get("themes_any") and not (set(pb["themes_any"]) & ctx.themes):
            continue
        if pb.get("event_types_any") and not (set(pb["event_types_any"]) & ctx.event_types):
            continue
        for eff in pb["effects"]:
            if _targets(eff, ctx):
                hits.append(
                    RuleHit(
                        pb["name"],
                        "playbook",
                        eff["direction"],
                        float(eff["weight"]),
                        f"matched '{phrase}'",
                    )
                )
    return hits


def adjustment_and_alignment(
    hits: list[RuleHit], ai_direction: str, cap: float
) -> tuple[float, float]:
    """(points to add to the score, macro_alignment 0-100) given the AI's direction."""
    if not hits:
        return 0.0, 50.0
    agree = sum(h.weight for h in hits if h.direction == ai_direction)
    disagree = sum(h.weight for h in hits if h.direction != ai_direction)
    adj = max(-cap, min(cap, agree - disagree))
    total = agree + disagree
    return adj, 100.0 * agree / total if total else 50.0
