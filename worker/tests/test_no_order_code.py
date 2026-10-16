"""Hard rule: paper trading only. No broker order-placement code anywhere in the worker."""

import re
from pathlib import Path

FORBIDDEN = re.compile(r"\b(place_order|modify_order|cancel_order|placeorder|create_order)\b", re.I)


def test_no_order_placement_code() -> None:
    app_dir = Path(__file__).resolve().parents[1] / "app"
    offenders = [
        str(p) for p in app_dir.rglob("*.py") if FORBIDDEN.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"order-placement code found: {offenders}"
