"""Regulator item -> stock name matching with a confidence score (Phase 8 owner rule).

Score per stock (best over its name, aliases and symbol), after normalising (lower case,
punctuation and company suffixes like 'Limited', 'Ltd', 'Pvt' removed):
  1.0  exact match of the whole entity name
  0.8  the alias appears as a whole phrase inside the entity name (multi-word or >= 6 letters)
  0.6  a short single-word alias inside the name
  up to 0.6  word overlap (Jaccard) for partial names
Two stocks within 0.05 of each other: ambiguous, the best is capped below the threshold.
Below `regulators.min_match_confidence` (deep_data.yaml): stored for review, no evidence.
"""

import re
from dataclasses import dataclass

from app.processing.entity_map import InstrumentInfo
from app.yaml_config import load_yaml

SUFFIXES = {
    "limited",
    "ltd",
    "pvt",
    "private",
    "inc",
    "incorporated",
    "co",
    "company",
    "corp",
    "corporation",
    "plc",
    "llp",
    "the",
    "and",
    "of",
}


def norm(text: str) -> list[str]:
    words = re.sub(r"[^a-z0-9& ]+", " ", text.lower()).split()
    return [w for w in words if w not in SUFFIXES]


@dataclass(frozen=True)
class Match:
    symbol: str
    score: float
    via: str


def threshold() -> float:
    return float(load_yaml("deep_data.yaml")["regulators"]["min_match_confidence"])


def _score(entity: list[str], name: str) -> float:
    n = norm(name)
    if not n or not entity:
        return 0.0
    if n == entity:
        return 1.0
    joined, phrase = f" {' '.join(entity)} ", f" {' '.join(n)} "
    if phrase in joined:
        return 0.8 if len(n) > 1 or len(n[0]) >= 6 else 0.6
    overlap = len(set(n) & set(entity)) / len(set(n) | set(entity))
    return round(0.6 * overlap, 3)


def match(entity: str, instruments: list[InstrumentInfo]) -> list[Match]:
    """Top candidates, best first; an ambiguous best is capped just below the threshold."""
    ent = norm(entity)
    out: list[Match] = []
    for inst in instruments:
        names = [inst.name, *inst.aliases, inst.symbol]
        best = max(((_score(ent, n), n) for n in names if n), default=(0.0, ""))
        if best[0] > 0:
            out.append(Match(inst.symbol, best[0], best[1]))
    out.sort(key=lambda m: -m.score)
    th = threshold()
    if len(out) > 1 and out[0].score >= th and out[0].score - out[1].score < 0.05:
        out[0] = Match(
            out[0].symbol, round(th - 0.01, 3), f"{out[0].via} (ambiguous with {out[1].symbol})"
        )
    return out[:3]
