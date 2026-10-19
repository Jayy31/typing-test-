"""Backtester CLI (run on the VM after the history backfill):

    python -m app.backtest compare [--from 2021-10-01 --to 2026-10-16] [--mode rules|replay]
    python -m app.backtest run --rules v21 --booking per_lot --spread 1.0 [--mode rules]

`compare` runs the standard set side by side: v2.1 rules vs the original fixed rules, per_lot vs
single_exit, spread x1.5 and x2, and the benchmarks (random direction, ATM without gates).
Results go to backtest_runs / backtest_trades, Parquet detail under HISTORY_DATA_DIR/backtests,
a markdown report there, and a short summary on Telegram.
"""

import argparse
import logging
import sys
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

from app.backtest import metrics, store
from app.backtest.data import HistoryData
from app.backtest.engine import Variant
from app.backtest.runner import run
from app.config import IST, get_settings
from app.db.session import session_scope
from app.history.store import connect
from app.notify.outbox import enqueue_alert
from app.yaml_config import load_yaml


def standard_variants(mode: str) -> list[Variant]:
    sens = [
        float(x) for x in load_yaml("backtest.yaml")["spread"]["sensitivity"] if float(x) != 1.0
    ]
    out = [
        Variant(f"{mode}: v2.1 rules, per_lot", mode),
        Variant(f"{mode}: fixed rules, per_lot", mode, rule_set="fixed"),
        Variant(f"{mode}: v2.1 rules, single_exit", mode, booking="single_exit"),
        *[Variant(f"{mode}: v2.1 rules, spread x{m:g}", mode, spread_mult=m) for m in sens],
    ]
    if mode == "rules":
        out.append(
            Variant("rules: legacy threshold (hit >= 50%, mean > 0)", mode, threshold="legacy")
        )
    if mode == "rules":
        depth = int(load_yaml("kg.yaml")["propagation"]["max_depth"])
        out += [  # Phase 8: knowledge-graph propagation (live default depth, and one hop more)
            Variant(f"rules: + knowledge graph ({depth} hops)", mode, kg_depth=depth),
            Variant(f"rules: + knowledge graph ({depth + 1} hops)", mode, kg_depth=depth + 1),
        ]
    if mode == "rules":  # Phase 12: market regime
        out += [
            Variant("rules: + regime-aware analogues", mode, regime_aware=True),
            Variant("rules: + positioning evidence", mode, positioning=True),
            *[
                Variant(f"rules: regime gates ({name})", mode, regime_gates=name)
                for name in load_yaml("regime.yaml")["backtest_variants"]
            ],
        ]
    if mode == "rules":
        out += [
            Variant("benchmark: random direction", mode, direction_mode="random"),
            Variant("benchmark: ATM, no gates", mode, selection="atm", direction_mode="signal"),
        ]
    return out


def execute(variants: list[Variant], start: date, end: date) -> str:
    rows = []
    with connect() as con:
        data = HistoryData(con)
        for v in variants:
            res = run(con, v, start, end, data=data)
            m = metrics.compute(res, data)
            run_id = store.save(res, m)
            store.save_equity(run_id, metrics.equity_points(res, data))
            rows.append((run_id, m))
            logging.info("%s: %d trades", v.name, len(res.trades))
        verdict = store.best_vs_default(rows)
        best_x2 = default_x2 = None
        if verdict.get("significant"):  # only then: does the advantage survive 2x spread?
            by_name = {v.name: v for v in variants}
            bv = by_name[verdict["best"]]
            res2 = run(
                con,
                replace(bv, name=f"{bv.name}, spread x2", spread_mult=2.0),
                start,
                end,
                data=data,
            )
            m2 = metrics.compute(res2, data)
            rows.append((store.save(res2, m2), m2))
            best_x2 = m2["all"].get("expectancy_pct")
            d2 = next(
                (
                    m
                    for _, m in rows
                    if m["variant"]["name"] == f"{variants[0].mode}: v2.1 rules, spread x2"
                ),
                None,
            )
            default_x2 = d2["all"].get("expectancy_pct") if d2 else None
    text = store.render(rows) + "\n\n" + store.verdict_text(verdict, best_x2, default_x2)
    out = (
        Path(get_settings().history_data_dir)
        / "backtests"
        / f"report_{datetime.now(IST):%Y%m%d_%H%M}.md"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    with session_scope() as s:
        summary = "\n".join(text.splitlines()[: 4 + len(rows)])
        enqueue_alert(
            s,
            "report",
            f"BACKTEST DONE ({start} to {end})\nFull report: {out}\n\n{summary}",
            f"backtest:{out.name}",
        )
    return text


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["compare", "run"])
    p.add_argument("--mode", choices=["rules", "replay"], default="rules")
    p.add_argument("--from", dest="start", type=date.fromisoformat)
    p.add_argument("--to", dest="end", type=date.fromisoformat)
    p.add_argument("--rules", choices=["fixed", "v21"], default="v21")
    p.add_argument("--booking", choices=["per_lot", "single_exit"], default="per_lot")
    p.add_argument("--spread", type=float, default=1.0)
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    end = a.end or datetime.now(IST).date()
    start = a.start or end.replace(year=end.year - int(load_yaml("history.yaml")["years"]))
    if a.cmd == "compare":
        variants = standard_variants(a.mode)
    else:
        variants = [
            Variant(
                f"{a.mode}: {a.rules}, {a.booking}, spread x{a.spread:g}",
                a.mode,
                a.rules,
                a.booking,
                a.spread,
            )
        ]
    print(execute(variants, start, end))
    return 0


if __name__ == "__main__":
    sys.exit(main())
