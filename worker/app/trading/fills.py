"""Realistic fill checks (DESIGN_V2 R2): could 1 lot actually fill at the quoted price?"""

ORDER = {"fillable": 0, "unknown": 1, "partial": 2, "unfillable": 3}


def classify(qty_at_best: int | None, needed: int) -> str:
    """fillable: best-level quantity covers the lot; partial: some; unfillable: none.
    unknown: the feed gave no quantity (counted separately in reports)."""
    if qty_at_best is None:
        return "unknown"
    if qty_at_best >= needed:
        return "fillable"
    return "partial" if qty_at_best > 0 else "unfillable"


def worse(a: str | None, b: str | None) -> str:
    vals = [x for x in (a, b) if x]
    return max(vals, key=lambda x: ORDER.get(x, 1)) if vals else "unknown"
