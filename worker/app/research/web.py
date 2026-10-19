"""Web allowlist for the research agent: config domains + the stock's company domains.

Company domains come from data/symbol_master.csv (`website`), corrected by data/ir_domains.yaml.
Fetched web content is untrusted: the agent extracts facts with citations and never follows
instructions found in a page (see the agent prompts); only URLs actually returned by the web
tools may be cited.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.collectors.fno_meta import SYMBOL_MASTER, parse_symbol_master
from app.yaml_config import load_yaml

IR_OVERRIDES = SYMBOL_MASTER.parent / "ir_domains.yaml"


def _host(d: str) -> str:
    d = d.strip().lower().removeprefix("https://").removeprefix("http://")
    return d.split("/", 1)[0].removeprefix("www.")


@lru_cache
def company_domains(
    path: Path = SYMBOL_MASTER, overrides: Path = IR_OVERRIDES
) -> dict[str, list[str]]:
    out = {
        r.symbol: [_host(r.website)]
        for r in parse_symbol_master(path.read_text(encoding="utf-8"))
        if r.website
    }
    if overrides.exists():
        data: dict[str, Any] = yaml.safe_load(overrides.read_text(encoding="utf-8")) or {}
        for sym, val in (data.get("overrides") or {}).items():
            vals = [] if val is None else [val] if isinstance(val, str) else list(val)
            out[str(sym).upper()] = [_host(v) for v in vals if v]
    return out


def allowed_domains(symbols: list[str] | None = None) -> tuple[str, ...]:
    base = [_host(d) for d in load_yaml("research_agent.yaml")["web_allowlist"]]
    comp = company_domains()
    for s in symbols or []:
        base += comp.get(s.upper(), [])
    return tuple(dict.fromkeys(base))


def url_allowed(url: str, domains: tuple[str, ...]) -> bool:
    host = _host(url)
    return any(host == d or host.endswith("." + d) for d in domains)
