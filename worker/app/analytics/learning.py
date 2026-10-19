"""Learning loop (Phase 10): monthly, evidence-based suggestions for YAML tunables.

Evidence = every closed trade (realised % of premium) and every resolved rejection (counterfactual
option return), each with the gate fields stored at the time. For a tunable and a candidate value,
an opportunity counts if it passes all gates with that value; per-opportunity difference vs the
current value is bootstrapped. A suggestion needs: the 97.5% interval of the mean difference above
0, a positive difference in both halves of the period, and still positive with a 2x-spread cost.
At most 2 per month, ranked by expected effect. The owner accepts on the dashboard; the worker
opens a PR editing the YAML, merges ONE accepted PR at the next month start, and reviews each
applied change after 2 months (revert suggested if it did not help).
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

import numpy as np
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.analytics import github, yaml_edit
from app.config import IST, get_settings
from app.db.models import PaperTrade, RejectedCandidate, Signal, Suggestion
from app.db.session import session_scope
from app.notify.outbox import enqueue_alert
from app.timeutil import as_ist
from app.yaml_config import CONFIG_DIR, load_yaml

log = logging.getLogger(__name__)


def cfg() -> dict[str, Any]:
    return dict(load_yaml("learning.yaml"))


@dataclass
class Opp:
    when: datetime
    ret: float  # % of premium (realised or counterfactual)
    values: dict[str, float | None] = field(default_factory=dict)
    taken: bool = False  # a real trade (it passed every gate at the time)


def opportunities(s: Session, start: datetime, end: datetime) -> list[Opp]:
    out: list[Opp] = []
    sig = {g.id: g for g in s.scalars(select(Signal))}
    # Phase 12: watchlist near-misses count with their real fills instead of the counterfactual
    use_watch = bool(load_yaml("watchlist.yaml")["learning"]["include"])
    watched: set[int] = set()
    for t in s.scalars(select(PaperTrade).where(PaperTrade.status == "CLOSED")):
        when = as_ist(t.exit_ts or t.entry_ts)
        g = sig.get(t.signal_id)
        if g is None or not (start <= when < end) or t.pnl_pct is None:
            continue
        if t.book != "main":
            if not use_watch:
                continue
            if g.analysis_id:
                watched.add(g.analysis_id)
        out.append(
            Opp(
                when,
                float(t.pnl_pct),
                {
                    "score": g.score,
                    "edge_ratio": g.edge_ratio,
                    "p_t1_before_sl": g.p_t1_before_sl,
                    "ev_pct": g.expected_value,
                },
                t.book == "main",  # only main-book trades passed every gate
            )
        )
    for r in s.scalars(
        select(RejectedCandidate).where(RejectedCandidate.outcome_status == "resolved")
    ):
        when = as_ist(r.ts)
        if r.option_return_pct is None or not (start <= when < end):
            continue
        if r.analysis_id in watched:
            continue  # already counted from the watchlist trade
        d = r.details_json or {}
        out.append(
            Opp(
                when,
                float(r.option_return_pct),
                {
                    "score": r.score,
                    "edge_ratio": d.get("edge_ratio"),
                    "p_t1_before_sl": d.get("p_t1_before_sl"),
                    "ev_pct": d.get("ev_pct"),
                },
                False,
            )
        )
    return sorted(out, key=lambda o: o.when)


def current_values() -> dict[str, float]:
    out = {}
    for t in cfg()["tunables"]:
        # the deployed image has only worker/: read the file by name from the config dir
        text = (CONFIG_DIR / t["file"].rsplit("/", 1)[-1]).read_text(encoding="utf-8")
        v = yaml_edit.get_value(text, t["path"])
        if v is not None:
            out[t["param"]] = float(v)
    return out


def selected(o: Opp, gates: dict[str, tuple[str, float]]) -> bool | None:
    """Passes all gates? None = unknowable (a rejection missing a gate field)."""
    for fld, th in gates.values():
        v = o.values.get(fld)
        if v is None:
            if o.taken:
                continue  # it passed this gate in real life
            return None
        if v < th:
            return False
    return True


def evaluate(
    opps: list[Opp], param: str, new: float, cur: dict[str, float], extra_cost: float = 0.0
) -> np.ndarray | None:
    """Per-opportunity difference (new - current) in % of premium; None if too few knowable."""
    tun = {t["param"]: t for t in cfg()["tunables"]}
    base = {p: (tun[p]["field"], v) for p, v in cur.items() if p in tun}
    alt = {**base, param: (tun[param]["field"], new)}
    diffs = []
    for o in opps:
        a, b = selected(o, base), selected(o, alt)
        if a is None or b is None:
            continue
        r = o.ret - extra_cost
        diffs.append((r if b else 0.0) - (r if a else 0.0))
    return np.array(diffs) if len(diffs) >= int(cfg()["min_opportunities"]) else None


def ci(diffs: np.ndarray, level: float) -> tuple[float, float]:
    c = cfg()
    rng = np.random.default_rng(int(c["bootstrap_seed"]))
    idx = rng.integers(0, len(diffs), (int(c["bootstrap_samples"]), len(diffs)))
    means = diffs[idx].mean(axis=1)
    tail = (1 - level) / 2 * 100
    lo, hi = np.percentile(means, [tail, 100 - tail])
    return float(lo), float(hi)


def candidate_changes(
    opps: list[Opp], cur: dict[str, float], months: float
) -> list[dict[str, Any]]:
    c = cfg()
    out = []
    for t in c["tunables"]:
        p = t["param"]
        if p not in cur:
            continue
        best = None
        for v in t["candidates"]:
            if float(v) == cur[p]:
                continue
            d = evaluate(opps, p, float(v), cur)
            if d is None or not d.any():
                continue
            lo, hi = ci(d, float(c["ci_level"]))
            half = len(d) // 2
            halves = [float(d[:half].mean()), float(d[half:].mean())]
            stressed = evaluate(opps, p, float(v), cur, float(c["stress_extra_cost_pct"]))
            stress_mean = float(stressed.mean()) if stressed is not None else -1.0
            ok = lo > 0 and min(halves) > 0 and stress_mean > 0
            effect = float(d.sum()) / months
            row = {
                "param": p,
                "file": t["file"],
                "path": t["path"],
                "current": cur[p],
                "proposed": float(v),
                "expected_effect": round(effect, 2),
                "evidence": {
                    "n": len(d),
                    "mean_diff_pct": round(float(d.mean()), 3),
                    "ci": [round(lo, 3), round(hi, 3)],
                    "ci_level": float(c["ci_level"]),
                    "halves": [round(h, 3) for h in halves],
                    "stress_mean_diff_pct": round(stress_mean, 3),
                    "months": months,
                },
            }
            row["_effect"], row["_step"] = effect, abs(float(v) - cur[p])
            # largest effect; on a tie the smaller change (less to undo if it does not hold up)
            better = (
                best is None
                or effect > best["_effect"] + 1e-9
                or (abs(effect - best["_effect"]) <= 1e-9 and row["_step"] < best["_step"])
            )
            if ok and better:
                best = row
        if best:
            best.pop("_effect")
            best.pop("_step")
            out.append(best)
    return sorted(out, key=lambda r: -r["expected_effect"])


def month_start(d: date) -> date:
    return d.replace(day=1)


def run_monthly(engine: Engine | None = None, now: datetime | None = None) -> int:
    """1st of the month (before the report): review applied changes, then new suggestions."""
    now = now or datetime.now(IST)
    c = cfg()
    month = month_start(now.date())
    start = datetime.combine(
        (month - timedelta(days=31 * int(c["lookback_months"]))).replace(day=1), time(0), IST
    )
    end = datetime.combine(month, time(0), IST)
    months = max(1.0, (end - start).days / 30.4)
    reverts = review_applied(engine, now)
    with session_scope(engine) as s:
        busy = {
            g.param
            for g in s.scalars(
                select(Suggestion).where(Suggestion.status.in_(("pending", "accepted")))
            )
        }
        recent = {
            g.param
            for g in s.scalars(select(Suggestion).where(Suggestion.status == "applied"))
            if g.applied_at
            and now - as_ist(g.applied_at) < timedelta(days=31 * int(c["review_after_months"]))
        }
        rows = [
            r
            for r in candidate_changes(opportunities(s, start, end), current_values(), months)
            if r["param"] not in busy | recent
        ]
        slots = int(c["max_suggestions_per_month"]) - len(reverts)
        added = 0
        for r in rows[: max(slots, 0)]:
            if s.scalar(
                select(Suggestion.id).where(
                    Suggestion.month == month,
                    Suggestion.param == r["param"],
                    Suggestion.kind == "change",
                )
            ):
                continue
            s.add(
                Suggestion(
                    month=month,
                    kind="change",
                    param=r["param"],
                    file=r["file"],
                    yaml_path=r["path"],
                    current_value=r["current"],
                    proposed_value=r["proposed"],
                    expected_effect=r["expected_effect"],
                    evidence_json=r["evidence"],
                    status="pending",
                )
            )
            added += 1
        if added + len(reverts):
            enqueue_alert(
                s,
                "suggestion",
                f"LEARNING LOOP\n{added + len(reverts)} new suggestion(s) for {month:%b-%Y}. "
                "Accept or reject them on the dashboard.",
                f"suggestions:{month}",
            )
    return added + len(reverts)


def review_applied(engine: Engine | None, now: datetime) -> list[int]:
    """2 months after a change: actual effect (same metric, new data) vs predicted; revert if it
    did not help. Returns the ids of revert suggestions created."""
    c = cfg()
    out = []
    with session_scope(engine) as s:
        due = [
            g
            for g in s.scalars(
                select(Suggestion).where(
                    Suggestion.status == "applied", Suggestion.review_json.is_(None)
                )
            )
            if g.applied_at
            and now - as_ist(g.applied_at) >= timedelta(days=30 * int(c["review_after_months"]))
        ]
        for g in due:
            since = as_ist(g.applied_at)  # type: ignore[arg-type]
            opps = opportunities(s, since, now)
            cur = {**current_values(), g.param: g.proposed_value}
            d = evaluate(opps, g.param, g.current_value, cur)  # old value vs the applied one
            months = max(1.0, (now - since).days / 30.4)
            if d is None:
                g.review_json = {"status": "too few opportunities", "reviewed_at": now.isoformat()}
                continue
            actual = float(-d.sum()) / months  # applied - old, per month
            lo, hi = ci(-d, float(c["ci_level"]))
            helped = actual > 0
            g.review_json = {
                "reviewed_at": now.isoformat(),
                "predicted_effect": g.expected_effect,
                "actual_effect": round(actual, 2),
                "ci": [round(lo, 3), round(hi, 3)],
                "n": len(d),
                "helped": helped,
            }
            if not helped:
                month = month_start(now.date())
                rv = Suggestion(
                    month=month,
                    kind="revert",
                    param=g.param,
                    file=g.file,
                    yaml_path=g.yaml_path,
                    current_value=g.proposed_value,
                    proposed_value=g.current_value,
                    expected_effect=round(-actual, 2),
                    status="pending",
                    reverts_id=g.id,
                    evidence_json={
                        "reason": "applied change did not help",
                        "predicted_effect": g.expected_effect,
                        "actual_effect": round(actual, 2),
                        "ci": [round(lo, 3), round(hi, 3)],
                        "n": len(d),
                    },
                )
                s.add(rv)
                s.flush()
                out.append(rv.id)
    return out


# ---- accept -> PR -> one merge per month -------------------------------------------------
def pr_body(g: Suggestion) -> str:
    e = g.evidence_json
    lines = [
        f"Learning-loop {g.kind} suggestion #{g.id} ({g.month:%b-%Y}), accepted on the dashboard.",
        "",
        f"- Parameter: `{g.yaml_path}` in `{g.file}`",
        f"- Change: {g.current_value:g} -> {g.proposed_value:g}",
        f"- Expected effect: {g.expected_effect:+.1f}% of premium per month",
    ]
    if "ci" in e:
        lines.append(
            f"- Evidence: n={e.get('n')}, mean difference "
            f"{e.get('mean_diff_pct', 'n/a')}% per opportunity, "
            f"{float(e.get('ci_level', cfg()['ci_level'])):.1%} interval "
            f"{e['ci'][0]:+.3f}..{e['ci'][1]:+.3f}"
        )
    if "halves" in e:
        lines.append(
            f"- Both halves positive: {e['halves']}; at 2x spread: {e.get('stress_mean_diff_pct')}"
        )
    if g.kind == "revert":
        lines.append(
            f"- Reverts #{g.reverts_id}: predicted {e.get('predicted_effect')}, "
            f"actual {e.get('actual_effect')}"
        )
    lines += ["", "Merged automatically at the start of the next month (one change per month)."]
    return "\n".join(lines)


def open_prs(engine: Engine | None = None, client: "github.GitHub | None" = None) -> int:
    """Accepted suggestions -> one PR each (if a token is configured; else record-only)."""
    client = client or github.from_settings()
    if client is None:
        return 0
    n = 0
    with session_scope(engine) as s:
        todo = s.scalars(
            select(Suggestion).where(
                Suggestion.status == "accepted", Suggestion.pr_number.is_(None)
            )
        ).all()
        for g in todo:
            content, sha = client.get_file(g.file)
            new = yaml_edit.set_value(content, g.yaml_path, g.proposed_value)
            branch = f"learning/{g.id}-{g.param.replace('_', '-')}"
            title = f"Learning loop: {g.param} {g.current_value:g} -> {g.proposed_value:g}"
            client.create_branch(branch)
            client.put_file(g.file, new, sha, branch, title)
            g.pr_number, g.pr_url = client.create_pr(branch, title, pr_body(g))
            n += 1
    return n


def merge_monthly(
    engine: Engine | None = None, now: datetime | None = None, client: "github.GitHub | None" = None
) -> int:
    """Month start: merge ONE accepted PR (reverts first, then the largest expected effect)."""
    now = now or datetime.now(IST)
    client = client or github.from_settings()
    if client is None:
        return 0
    start = datetime.combine(month_start(now.date()), time(0), IST)
    with session_scope(engine) as s:
        done = s.scalars(select(Suggestion).where(Suggestion.applied_at.is_not(None))).all()
        if any(g.applied_at and as_ist(g.applied_at) >= start for g in done):
            return 0  # one change per month, even if the job runs again
        ready = [
            g
            for g in s.scalars(
                select(Suggestion).where(
                    Suggestion.status == "accepted", Suggestion.pr_number.is_not(None)
                )
            )
            if g.decided_at and as_ist(g.decided_at) < start
        ]
        if not ready:
            return 0
        g = sorted(ready, key=lambda x: (x.kind != "revert", -x.expected_effect))[0]
        client.merge_pr(int(g.pr_number))  # type: ignore[arg-type]
        g.status, g.applied_at = "applied", now
        enqueue_alert(
            s,
            "suggestion",
            f"CONFIG CHANGE APPLIED\n{g.param}: {g.current_value:g} -> {g.proposed_value:g} "
            f"(PR #{g.pr_number}). Effect reviewed in {cfg()['review_after_months']} months.",
            f"applied:{g.id}",
        )
    return 1


def configured() -> bool:
    st = get_settings()
    return bool(st.github_token_config_pr and st.github_config_repo)
