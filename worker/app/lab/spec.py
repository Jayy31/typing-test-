"""Strategy spec (versioned YAML) validated against a schema that encodes the core rules.

There is no field that could express a forbidden trade: the option type follows the direction
(bullish -> long call, bearish -> long put), one leg, buy only, monthly expiry chosen by the core
rule (current, or next when too close / the event falls after it). Index symbols are rejected.
Every trade a spec produces still goes through `core_rules.violation` at entry.
"""

import hashlib
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app import taxonomy
from app.trading import core_rules
from app.yaml_config import load_yaml


def cfg() -> dict[str, Any]:
    return dict(load_yaml("strategy_lab.yaml"))


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Trigger(_M):
    event_types: list[str] = Field(min_length=1, description="taxonomy event types / subtypes")
    direction: Literal["from_event", "base_rate", "bullish", "bearish"] = "from_event"
    graph_hops: Literal[0, 2, 3] = 0  # 2nd/3rd-order: linked stocks via the knowledge graph
    min_analogue_n: int = Field(default=0, ge=0, le=500)  # 0 = no analogue requirement

    @field_validator("event_types")
    @classmethod
    def _known(cls, v: list[str]) -> list[str]:
        names = set(taxonomy.event_types()) | set(taxonomy.structured_types())
        bad = [e for e in v if e not in names and not e.startswith("kg:")]
        if bad:
            raise ValueError(f"unknown event types {bad}")
        return v


class Filters(_M):
    regimes: list[str] = []  # 'calm', 'normal', 'stressed', 'risk_on', 'risk_off' or full labels
    positioning: dict[str, list[str]] = {}  # feature -> allowed states
    sectors: list[str] = []
    exclude_sectors: list[str] = []
    symbols: list[str] = []
    iv_percentile_min: float | None = Field(default=None, ge=0, le=100)
    iv_percentile_max: float | None = Field(default=None, ge=0, le=100)
    min_adv_cr: float | None = Field(default=None, ge=0)  # liquidity / size proxy
    large_cap_only: bool = False

    @field_validator("symbols")
    @classmethod
    def _no_index(cls, v: list[str]) -> list[str]:
        bad = [s for s in v if core_rules.is_index(s)]
        if bad:
            raise ValueError(f"index symbols are never traded: {bad}")
        return [s.upper() for s in v]


class OptionChoice(_M):
    strike: Literal["atm", "itm1", "itm2", "otm1", "delta"] = "atm"
    target_delta: float | None = Field(default=None, gt=0, lt=1)
    expiry: Literal["monthly_rule"] = "monthly_rule"  # the only allowed value (core rule)

    @model_validator(mode="after")
    def _delta(self) -> "OptionChoice":
        lo, hi = cfg()["spec"]["delta_range"]
        if self.strike == "delta":
            if self.target_delta is None or not lo <= self.target_delta <= hi:
                raise ValueError(f"strike 'delta' needs target_delta in [{lo}, {hi}]")
        elif self.target_delta is not None:
            raise ValueError("target_delta only with strike 'delta'")
        return self


class Entry(_M):
    timing: Literal["event_close", "next_open", "before_event"] = "event_close"
    days_before: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _before(self) -> "Entry":
        mx = int(cfg()["spec"]["max_days_before_event"])
        if self.timing == "before_event":
            if self.days_before is None or self.days_before > mx:
                raise ValueError(f"before_event needs days_before in 1..{mx}")
        elif self.days_before is not None:
            raise ValueError("days_before only with timing 'before_event'")
        return self


class Exits(_M):
    rule_set: Literal["fixed", "v21"] = "v21"
    booking: Literal["per_lot", "single_exit"] = "per_lot"
    stop_loss_pct: float | None = Field(default=None, ge=-90, le=-10)
    targets_pct: list[float] | None = Field(default=None, min_length=1, max_length=4)
    exit_before_event: bool = False  # close the day before the scheduled event
    max_hold_days: int | None = Field(default=None, ge=1)
    iv_spike_exit_pts: float | None = Field(default=None, gt=0, le=50)

    @field_validator("targets_pct")
    @classmethod
    def _targets(cls, v: list[float] | None) -> list[float] | None:
        if v is not None and (any(x <= 0 for x in v) or v != sorted(v)):
            raise ValueError("targets must be positive and increasing")
        return v

    @model_validator(mode="after")
    def _hold(self) -> "Exits":
        if self.max_hold_days and self.max_hold_days > int(cfg()["spec"]["max_hold_days"]):
            raise ValueError("max_hold_days above the configured maximum")
        return self

    def overrides(self) -> dict[str, Any]:
        over: dict[str, Any] = dict(load_yaml("backtest.yaml")["rule_sets"][self.rule_set])
        over["booking_mode"] = self.booking
        if self.stop_loss_pct is not None:
            over["stop_loss_pct"] = float(self.stop_loss_pct)
        if self.targets_pct is not None:
            over["targets_pct"] = tuple(float(x) for x in self.targets_pct)
            over["targets_mode"] = "fixed"
        return over


class StrategySpec(_M):
    name: str = Field(pattern=r"^[a-z0-9_]{3,48}$")
    version: int = Field(default=1, ge=1)
    source: Literal["data_mining", "known_pattern", "system_evidence", "ai_proposal", "news_core"]
    description: str = ""
    trigger: Trigger
    filters: Filters = Filters()
    option: OptionChoice = OptionChoice()
    entry: Entry = Entry()
    exits: Exits = Exits()

    @model_validator(mode="after")
    def _consistent(self) -> "StrategySpec":
        sched = set(cfg()["spec"]["scheduled_event_types"])
        if self.entry.timing == "before_event" and not set(self.trigger.event_types) <= sched:
            raise ValueError(f"before_event entries need scheduled event types {sorted(sched)}")
        if self.entry.timing == "before_event" and self.trigger.direction == "from_event":
            raise ValueError(
                "before_event entries happen before the outcome: direction must be "
                "bullish, bearish or base_rate"
            )
        if self.exits.exit_before_event and self.entry.timing != "before_event":
            raise ValueError("exit_before_event only with a before_event entry")
        if self.trigger.direction == "base_rate" and self.trigger.min_analogue_n < 1:
            raise ValueError("direction from base_rate needs min_analogue_n >= 1")
        return self

    @staticmethod
    def option_type(direction: str) -> str:
        """The only mapping there is: bullish -> long call, bearish -> long put."""
        return {"bullish": "CE", "bearish": "PE"}[direction]

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json"), sort_keys=True)

    def spec_hash(self) -> str:
        body = self.model_dump(mode="json")
        body.pop("version", None)
        body.pop("description", None)
        return hashlib.sha256(yaml.safe_dump(body, sort_keys=True).encode()).hexdigest()[:16]


def parse(text: str) -> StrategySpec:
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("spec must be a YAML mapping")
    return StrategySpec.model_validate(data)
