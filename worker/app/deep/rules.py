"""Pure rules for structured events: counterparty class, subtype, materiality
(config: deep_data.yaml)."""

from dataclasses import dataclass, field
from typing import Any

from app.yaml_config import load_yaml


def _cfg() -> dict[str, Any]:
    return load_yaml("deep_data.yaml")


def insider_counterparty(category: str | None) -> str:
    """NSE PIT person category -> promoter / promoter_group / director_kmp / other.
    'Promoter Group' is checked before 'Promoter' (longest pattern first)."""
    low = (category or "").lower()
    pats = _cfg()["counterparty"]["insider_category"]
    for cls in sorted(pats, key=lambda c: -max(len(p) for p in pats[c])):
        if any(p in low for p in pats[cls]):
            return str(cls)
    return "other"


def deal_counterparty(client: str | None, promoters: set[str] | None = None) -> str:
    """Bulk/block deal client -> promoter / promoter_group / fii / mf_dii / other.
    `promoters`: known promoter names (upper-case) from insider filings for this stock."""
    name = (client or "").strip()
    up = name.upper()
    if promoters and up in promoters:
        return "promoter"
    low = f" {name.lower()} "
    pats = _cfg()["counterparty"]["deal_client"]
    for cls in ("mf_dii", "fii"):  # domestic fund names are more specific than foreign patterns
        if any(p in low for p in pats[cls]):
            return cls
    return "other"


def side_of(text: str | None) -> str | None:
    low = (text or "").strip().lower()
    if low == "b" or low.startswith(("buy", "purchase", "acqui")):
        return "buy"
    if low == "s" or low.startswith(("sell", "sale", "dispos")):
        return "sell"
    return None


@dataclass
class Materiality:
    material: bool
    reasons: list[str] = field(default_factory=list)
    pct_adv: float | None = None
    pct_mcap: float | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "material": self.material,
            "reasons": self.reasons,
            "pct_adv": self.pct_adv,
            "pct_mcap": self.pct_mcap,
        }


def value_materiality(
    category: str, value_inr: float | None, adv20_inr: float | None, mcap_inr: float | None
) -> Materiality:
    """Rs value >= minimum AND (value >= x% of 20-day avg traded value OR >= y% of market cap).
    With neither ADV nor market cap known, only the Rs minimum applies (reason recorded)."""
    m = _cfg()["materiality"][category]
    if not value_inr or value_inr <= 0:
        return Materiality(False, ["value unknown"])
    pct_adv = 100 * value_inr / adv20_inr if adv20_inr else None
    pct_mcap = 100 * value_inr / mcap_inr if mcap_inr else None
    reasons: list[str] = []
    if value_inr < float(m["min_value_inr"]):
        reasons.append(
            f"value Rs {value_inr / 1e7:.2f} cr below Rs {m['min_value_inr'] / 1e7:.2f} cr"
        )
        return Materiality(False, reasons, pct_adv, pct_mcap)
    if pct_adv is None and pct_mcap is None:
        return Materiality(True, ["no ADV / market cap yet: Rs minimum only"], None, None)
    big_adv = pct_adv is not None and pct_adv >= float(m["min_pct_adv"])
    big_mcap = pct_mcap is not None and pct_mcap >= float(m["min_pct_mcap"])
    if big_adv:
        reasons.append(f"{pct_adv:.1f}% of 20-day avg traded value")
    if big_mcap:
        reasons.append(f"{pct_mcap:.3f}% of market cap")
    if not (big_adv or big_mcap):
        reasons.append(f"below {m['min_pct_adv']}% of ADV and {m['min_pct_mcap']}% of market cap")
    return Materiality(big_adv or big_mcap, reasons, pct_adv, pct_mcap)


def shareholding_subtypes(prev: dict[str, float | None], cur: dict[str, float | None]) -> list[str]:
    """Quarter-on-quarter stake changes above the thresholds -> holding_* / pledge_* subtypes."""
    m = _cfg()["materiality"]["shareholding"]
    out: list[str] = []
    for key, cls in (("promoter_pct", "promoter"), ("fii_pct", "fii"), ("dii_pct", "mf_dii")):
        a, b = prev.get(key), cur.get(key)
        if a is None or b is None:
            continue
        if b - a >= float(m["min_change_pp"]):
            out.append(f"holding_up_{cls}")
        elif a - b >= float(m["min_change_pp"]):
            out.append(f"holding_down_{cls}")
    a, b = prev.get("pledge_pct"), cur.get("pledge_pct")
    if a is not None and b is not None:
        if b - a >= float(m["pledge_min_change_pp"]):
            out.append("pledge_increase")
        elif a - b >= float(m["pledge_min_change_pp"]):
            out.append("pledge_decrease")
    return out
