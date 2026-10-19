"""Live AI smoke test: one full panel, one single-analyst call and one post-mortem on a fixed
sample news cluster, with the real models configured in env / config/ai.yaml.

    make smoke-ai                      # manual run (always runs)
    python -m app.ai.smoke --startup   # what the worker does at startup: only until it passes

Per call it records: valid on the first try / attempts, input, output, estimated thinking and
cached tokens, actual Rs cost vs the estimate in ai.yaml `smoke.estimates_inr`, and duration;
plus the wall time of the full panel. Hard cost limit (`smoke.limit_inr`, Rs 50): every call
reserves its worst case (prompt + max_tokens output) before it is sent and is skipped if the
reservation would cross the limit, so the limit holds even with retries and parallel calls.

Auto-fixes (owner rule): a role that fails its schema gets effort `low` (config-table override
`ai.effort.<role>`); a panel slower than `smoke.max_panel_seconds` gets effort `low` on its
slowest mid/strong roles. After a fix the failing part is re-run once within the same limit.
Results go to `ai_smoke_runs` and to Telegram.
"""

import argparse
import json
import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Engine, func, select

from app.ai import analyst
from app.ai.client import AiClient, AiOutputError, CallUsage, price_table
from app.ai.panel import AgentFailed, AgentStep, run_panel, run_validated
from app.ai.schemas import AnalystOutput, PostMortemOutput
from app.config import IST, get_settings
from app.db.config_store import get_config, set_config
from app.db.models import AiSmokeRun, Analysis, PaperTrade, Scenario, Signal, TradeEvent
from app.db.session import session_scope
from app.notify.outbox import enqueue_alert
from app.tracking import postmortem
from app.yaml_config import load_yaml

log = logging.getLogger(__name__)
PANEL_ROLES = ("researcher", "novelty", "bull", "bear", "judge")
SAMPLE_IDS = {910001, 910002, 910003}


class CostLimitReached(AiOutputError):
    final = True  # no retry


def _cfg() -> dict[str, Any]:
    return dict(load_yaml("ai.yaml")["smoke"])


class GuardedClient(AiClient):
    """AiClient that reserves each call's worst-case cost against a hard limit."""

    def __init__(self, base: AiClient, limit_inr: float) -> None:
        super().__init__(base.settings, base.engine, base._msg_override, base._batch_override)
        self.limit, self.spent, self.reserved = limit_inr, 0.0, 0.0
        self.calls: list[tuple[str, CallUsage]] = []
        self._lock = threading.Lock()

    def worst_case_inr(self, role: str, system: str, user: str, max_tokens: int) -> float:
        prompt_tokens = (len(system) + len(user)) // 3 + 200  # generous chars-per-token
        p = price_table(role, self.model_for(role), prompt_tokens)
        usd = (prompt_tokens * max(p["input"], p["cache_write"]) + max_tokens * p["output"]) / 1e6
        return usd * float(load_yaml("ai.yaml")["usd_inr"])

    def parse_with_usage[T: BaseModel](
        self,
        role: str,
        system: str,
        user: str,
        output: type[T],
        max_tokens: int,
        thinking: dict[str, Any] | None = None,
    ) -> tuple[T, CallUsage]:
        worst = self.worst_case_inr(role, system, user, max_tokens)
        with self._lock:
            if self.spent + self.reserved + worst > self.limit:
                raise CostLimitReached(
                    f"skipped: Rs {self.spent:.2f} spent + Rs {worst:.2f} worst case would exceed the Rs {self.limit:.0f} limit"
                )
            self.reserved += worst
        try:
            out, usage = super().parse_with_usage(role, system, user, output, max_tokens, thinking)
        finally:
            with self._lock:
                self.reserved -= worst
        with self._lock:
            self.spent += usage.cost_inr
            self.calls.append((role, usage))
        return out, usage


# ---- fixed samples ------------------------------------------------------------------------
def sample_message(today: datetime) -> str:
    d = today.strftime("%Y-%m-%d")
    return (
        f"Today: {d} 10:30 IST\n"
        "Stock: BHARTIARTL (Bharti Airtel), sector Telecom, sub-sector Telecom Services, lot size 475\n\n"
        "NEWS EVIDENCE:\n"
        f"[id=910001] nse_announcements (official), {d} 08:05, tagger sentiment +0.80\n"
        "  BHARTIARTL: Revision in mobile tariffs\n"
        "  Bharti Airtel announced a 10-12% increase in prepaid and postpaid mobile tariffs effective in 7 days.\n"
        f"[id=910002] economic_times_markets (media), {d} 08:40, tagger sentiment +0.70\n"
        "  Airtel hikes tariffs; analysts see ARPU rising to Rs 245\n"
        "  Brokerages estimate a 6-8% ARPU uplift over two quarters if competitors follow.\n"
        f"[id=910003] livemint_markets (media), {d} 09:10, tagger sentiment +0.40\n"
        "  Jio yet to respond to Airtel tariff move\n"
        "  Reliance Jio has not announced any change; past hikes were matched within two weeks.\n\n"
        "SENTIMENT: weighted +0.68 over 3 item(s), 3 source(s), official filing present: True\n\n"
        "SECTOR CONTEXT:\n- (sector-level: Telecom): sentiment +0.40 from 1 item(s)\n\n"
        "MACRO:\n- India VIX 13.8; Nifty flat\n\n"
        "UPCOMING EVENTS:\n- none in the next 30 days\n\n"
        "RULES ENGINE (for your information):\n- none\n\n"
        "HISTORICAL ANALOGUES (event study, actual prices):\nnone available (smoke test)"
    )


def sample_trade() -> tuple[PaperTrade, Signal, Analysis, list[Scenario], list[TradeEvent]]:
    now = datetime.now(IST)
    an = Analysis(
        id=0,
        symbol="BHARTIARTL",
        thesis="Tariff hike lifts ARPU; peers likely to follow.",
        direction="bullish",
        risks=["Jio does not follow"],
        model="sample",
        prediction_source="panel",
    )
    sig = Signal(
        symbol="BHARTIARTL",
        direction="bullish",
        score=78,
        status="CLOSED",
        predicted_move_pct=2.1,
        predicted_move_raw_pct=4.2,
        implied_move_pct=1.6,
        edge_ratio=1.31,
        p_t1_before_sl=0.58,
        calibration_method="shrink",
        expected_value=14.0,
        iv_percentile=62,
    )
    tr = PaperTrade(
        signal_id=0,
        symbol="BHARTIARTL",
        option_type="CE",
        strike=1600,
        expiry=date.today() + timedelta(days=20),
        lots=3,
        lot_size=475,
        booking_mode="per_lot",
        entry_ts=now - timedelta(days=3),
        entry_premium=40.0,
        spot_entry=1590,
        exit_ts=now,
        exit_premium=18.5,
        spot_exit=1572,
        exit_reason="SL",
        pnl=-30960,
        pnl_pct=-53.8,
        slippage_inr=-1710,
        mfe_pct=12.0,
        mae_pct=-53.8,
        fillability="fillable",
        attribution_json={
            "delta": -9000,
            "theta": -6200,
            "vega": -14100,
            "spread": -1660,
            "residual": 0,
        },
        benchmark_stock_pct=-1.1,
        sl=20,
        t1=50,
        t2=60,
        t3=80,
        status="CLOSED",
    )
    scen = [
        Scenario(
            name="hike sticks",
            probability=0.55,
            stock_move_pct=4.5,
            timing_days=3,
            triggers=[],
            invalidation="",
        ),
        Scenario(
            name="muted",
            probability=0.35,
            stock_move_pct=0.5,
            timing_days=3,
            triggers=[],
            invalidation="",
        ),
        Scenario(
            name="sell the news",
            probability=0.10,
            stock_move_pct=-3.0,
            timing_days=2,
            triggers=[],
            invalidation="",
        ),
    ]
    ev = [
        TradeEvent(ts=now - timedelta(days=3), event="ENTRY", premium=40.0, lots=3, spot=1590),
        TradeEvent(
            ts=now,
            event="SL",
            premium=18.5,
            lots=3,
            trigger_level=20.0,
            gap=True,
            slippage_inr=-1710,
            spot=1572,
        ),
    ]
    return tr, sig, an, scen, ev


# ---- running ------------------------------------------------------------------------------
@dataclass
class SmokeResult:
    calls: list[dict[str, Any]] = field(default_factory=list)
    panel_seconds: float | None = None
    panel_ok: bool = False
    single_ok: bool = False
    postmortem_ok: bool = False
    agent: dict[str, Any] | None = None  # Phase 13 research-agent run (reported separately)
    fixes: list[str] = field(default_factory=list)
    total_cost_inr: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        slow = self.panel_seconds is not None and self.panel_seconds > float(
            _cfg()["max_panel_seconds"]
        )
        return self.panel_ok and self.single_ok and self.postmortem_ok and not slow


def _visible_tokens(output: Any) -> int:
    return len(json.dumps(output, ensure_ascii=False)) // 4 if output is not None else 0


def call_row(
    step: AgentStep | None,
    role: str,
    run: str,
    error: str | None = None,
    attempts: int = 0,
    cost: float = 0.0,
    took: float = 0.0,
) -> dict[str, Any]:
    est = float(_cfg()["estimates_inr"].get(role, 0.0))
    u = step.usage if step else None
    out_tokens = u.output_tokens if u else 0
    return {
        "run": run,
        "role": role,
        "model": u.model if u else None,
        "effort": u.effort if u else None,
        "ok": step is not None,
        "first_try": bool(step and step.attempts == 1),
        "attempts": step.attempts if step else attempts,
        "error": error,
        "input_tokens": u.input_tokens if u else 0,
        "output_tokens": out_tokens,
        "thinking_tokens_est": max(0, out_tokens - _visible_tokens(step.output if step else None)),
        "cache_read": u.cache_read if u else 0,
        "cache_write": u.cache_write if u else 0,
        "cost_inr": round(step.cost_inr if step else cost, 3),
        "estimate_inr": est,
        "seconds": step.duration_s if step else round(took, 2),
    }


def run_panel_part(ai: GuardedClient, res: SmokeResult, run: str) -> None:
    t0 = time.monotonic()
    panel = run_panel(ai, sample_message(datetime.now(IST)), SAMPLE_IDS, parallel=True)
    res.panel_seconds = round(time.monotonic() - t0, 1)
    for st in panel.steps:
        res.calls.append(call_row(st, st.agent, run))
    if panel.failed_agent:
        res.calls.append(
            call_row(
                None,
                panel.failed_agent,
                run,
                (panel.error or "").removeprefix("panel failed: "),
                panel.failed_attempts,
            )
        )
    elif panel.error:
        res.notes.append(panel.error)
    res.panel_ok = panel.out is not None


def run_single_part(ai: GuardedClient, res: SmokeResult, run: str) -> None:
    try:
        _, st = run_validated(
            ai,
            "analyst",
            analyst.SYSTEM_PROMPT,
            analyst.PROMPT_VERSION,
            sample_message(datetime.now(IST)),
            AnalystOutput,
            lambda o: analyst.validate_output(o, SAMPLE_IDS),
        )
        res.calls.append(call_row(st, "analyst", run))
        res.single_ok = True
    except AgentFailed as exc:
        res.calls.append(
            call_row(None, "analyst", run, exc.error, exc.attempts, exc.cost_inr, exc.duration_s)
        )


def run_postmortem_part(ai: GuardedClient, res: SmokeResult, run: str) -> None:
    tr, sig, an, scen, ev = sample_trade()
    msg = postmortem.build_message(tr, sig, an, scen, ev, ["Jio says no tariff change for now"])
    try:
        _, st = run_validated(
            ai,
            "postmortem",
            postmortem.PROMPT,
            postmortem.PROMPT_VERSION,
            msg,
            PostMortemOutput,
            lambda o: None,
        )
        res.calls.append(call_row(st, "postmortem", run))
        res.postmortem_ok = True
    except AgentFailed as exc:
        res.calls.append(
            call_row(None, "postmortem", run, exc.error, exc.attempts, exc.cost_inr, exc.duration_s)
        )


def run_agent_part(res: SmokeResult, engine: Engine | None) -> None:
    """One research-agent investigation on the sample (research_live role of the current preset),
    under its own hard limit. Reported on its own line; it does not decide `passed` (the
    agent is behind feat.research_agent, and startup re-runs must not repeat its cost)."""
    from dataclasses import replace

    from app.research.agent import run_live
    from app.research.config import role_config

    cfg = _cfg()
    limit = float(cfg.get("agent_limit_inr", 30))
    try:
        rc = role_config("research_live")
    except (RuntimeError, ValueError) as exc:
        res.agent = {"ok": False, "error": f"not configured: {exc}"}
        return
    rc = replace(rc, max_inr=min(rc.max_inr, limit - float(cfg.get("agent_margin_inr", 5))))
    t0 = time.monotonic()
    try:
        r = run_live(
            rc,
            "BHARTIARTL",
            "Telecom",
            ["tariff_change"],
            sample_message(datetime.now(IST)),
            set(SAMPLE_IDS),
            engine,
            kind="smoke",
        )
    except Exception as exc:  # noqa: BLE001 - report, never crash the smoke test
        res.agent = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
        return
    res.agent = {
        "ok": r.out is not None,
        "status": r.status,
        "error": r.error,
        "model": r.model,
        "preset": r.preset,
        "cost_inr": round(r.cost_inr, 2),
        "limit_inr": limit,
        "seconds": round(time.monotonic() - t0, 1),
        "tools": r.tools_used,
        "run_id": r.run_id,
    }


def apply_fixes(res: SmokeResult, engine: Engine | None) -> list[str]:
    """Owner rule 4: lower effort for roles that failed their schema or made the panel too slow."""
    fixes: list[str] = []
    limit = float(_cfg()["max_panel_seconds"])
    failed = {
        c["role"]
        for c in res.calls
        if not c["ok"] and c["error"] and not str(c["error"]).startswith("skipped")
    }
    with session_scope(engine) as s:
        for role in sorted(failed):
            key = f"ai.effort.{role}"
            if get_config(s, key) != "low":
                set_config(s, key, "low")
                fixes.append(
                    f"{role}: failed its schema with thinking on -> effort set to low ({key})"
                )
            else:
                fixes.append(
                    f"{role}: failed its schema even at effort low -> needs attention (not fixed)"
                )
        if res.panel_seconds is not None and res.panel_seconds > limit:
            slow = sorted(
                (c for c in res.calls if c["role"] in ("bull", "bear", "judge") and c["ok"]),
                key=lambda c: -c["seconds"],
            )
            for c in slow[:2]:
                key = f"ai.effort.{c['role']}"
                if get_config(s, key) != "low":
                    set_config(s, key, "low")
                    fixes.append(
                        f"{c['role']}: {c['seconds']:.0f}s, panel {res.panel_seconds:.0f}s > {limit:.0f}s -> effort set to low ({key})"
                    )
    return fixes


def run_smoke(
    engine: Engine | None = None,
    trigger: str = "manual",
    base: AiClient | None = None,
    agent: bool | None = None,
) -> SmokeResult:
    """`agent`: also run one research-agent investigation (default: only against the real API,
    not when `base` wraps a fake messages client)."""
    cfg = _cfg()
    base = base or AiClient(engine=engine)
    ai = GuardedClient(base, float(cfg["limit_inr"]))
    res = SmokeResult()
    started = datetime.now(IST)
    run_panel_part(ai, res, "first")
    run_single_part(ai, res, "first")
    run_postmortem_part(ai, res, "first")
    if not res.passed:
        res.fixes = apply_fixes(res, engine)
        if res.fixes:  # re-run what failed, once, within the same Rs limit
            if not res.panel_ok or (res.panel_seconds or 0) > float(cfg["max_panel_seconds"]):
                run_panel_part(ai, res, "after_fix")
            if not res.single_ok:
                run_single_part(ai, res, "after_fix")
            if not res.postmortem_ok:
                run_postmortem_part(ai, res, "after_fix")
    if agent if agent is not None else base._msg_override is None:
        run_agent_part(res, engine)
    res.total_cost_inr = round(ai.spent + float((res.agent or {}).get("cost_inr") or 0), 2)
    with session_scope(engine) as s:
        s.add(
            AiSmokeRun(
                started_at=started,
                finished_at=datetime.now(IST),
                trigger=trigger,
                passed=res.passed,
                total_cost_inr=res.total_cost_inr,
                panel_seconds=res.panel_seconds,
                results_json={
                    "calls": res.calls,
                    "notes": res.notes,
                    "limit_inr": cfg["limit_inr"],
                    "agent": res.agent,
                },
                fixes_json=res.fixes,
            )
        )
        enqueue_alert(s, "health", render(res), f"ai_smoke:{started.isoformat()}")
    return res


def render(res: SmokeResult) -> str:
    head = "AI SMOKE TEST: " + ("PASSED" if res.passed else "FAILED")
    lines = [
        head,
        f"Total cost Rs {res.total_cost_inr:.2f} (limit Rs {_cfg()['limit_inr']}); full panel "
        f"{res.panel_seconds if res.panel_seconds is not None else 'n/a'}s (limit {_cfg()['max_panel_seconds']}s)",
        "",
    ]
    for c in res.calls:
        status = (
            ("ok, first try" if c["first_try"] else f"ok after {c['attempts']} attempts")
            if c["ok"]
            else f"FAILED ({c['error']})"
        )
        lines.append(
            f"{c['role']}{' (after fix)' if c['run'] == 'after_fix' else ''} [{c['model']}, effort {c['effort']}]: {status}; "
            f"in {c['input_tokens']}, out {c['output_tokens']} (thinking ~{c['thinking_tokens_est']}), cached {c['cache_read']}/{c['cache_write']}; "
            f"Rs {c['cost_inr']:.2f} vs est Rs {c['estimate_inr']:.2f}; {c['seconds']:.1f}s"
        )
    if res.agent is not None:
        a = res.agent
        if a.get("ok"):
            lines.append(
                f"research agent [{a['model']}, preset {a['preset']}]: ok ({a['status']}); "
                f"Rs {a['cost_inr']:.2f} (own limit Rs {a['limit_inr']:.0f}); {a['seconds']:.0f}s; "
                f"tools: {', '.join(a['tools']) or 'none'}"
            )
        else:
            lines.append(
                f"research agent: FAILED ({a.get('error')}) - reported only; "
                "the panel/single analyst still work"
            )
    if res.fixes:
        lines += ["", "AUTO-FIXES APPLIED:"] + [f"- {f}" for f in res.fixes]
    lines += [f"Note: {n}" for n in res.notes]
    return "\n".join(lines)


def startup_check(engine: Engine | None = None) -> str:
    """Worker startup: run once until a run passes (max `max_auto_attempts` failed runs)."""
    st = get_settings()
    if not st.anthropic_api_key:
        return "skipped: ANTHROPIC_API_KEY not set"
    base = AiClient(engine=engine)
    missing = [r for r in (*PANEL_ROLES, "analyst") if not base.configured(r)]
    if missing:
        return f"skipped: AI_MODEL_* not set for {', '.join(missing)}"
    with session_scope(engine) as s:
        if s.scalar(select(func.count(AiSmokeRun.id)).where(AiSmokeRun.passed.is_(True))):
            return "skipped: already passed"
        failed = int(
            s.scalar(select(func.count(AiSmokeRun.id)).where(AiSmokeRun.trigger == "startup")) or 0
        )
    if failed >= int(_cfg()["max_auto_attempts"]):
        return f"skipped: {failed} startup runs failed; run `make smoke-ai` after fixing"
    res = run_smoke(engine, "startup", base)
    return "passed" if res.passed else "failed"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--startup", action="store_true", help="only run if no run has passed yet")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    if a.startup:
        print(startup_check())
        return 0
    res = run_smoke()
    print(render(res))
    return 0 if res.passed else 1


if __name__ == "__main__":
    sys.exit(main())
