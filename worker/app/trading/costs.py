"""Trading cost model for paper P&L (same settings the simulator uses)."""

from app.yaml_config import load_yaml


def trade_costs_inr(entry_premium: float, lot_size: int, lots: int, orders: int) -> float:
    """Percentage costs on the entry value (STT, exchange, slippage beyond spread) plus brokerage
    per executed order (entry + each partial exit)."""
    c = load_yaml("options.yaml")["simulator"]
    value = entry_premium * lot_size * lots
    return (
        value * float(c["costs_pct_of_premium"]) / 100
        + float(c["brokerage_inr_per_order"]) * orders
    )
