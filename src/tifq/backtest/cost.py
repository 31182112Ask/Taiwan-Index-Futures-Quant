"""Conservative V1 execution cost helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

OrderSide = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class CostModel:
    """Cost assumptions for one TMF backtest run."""

    point_value: float = 10
    commission_per_side: float = 0
    tax_rate: float = 0
    slippage_points_per_side: float = 0


@dataclass(frozen=True)
class OrderCost:
    """One-side execution costs in account currency."""

    fee: float
    tax: float
    slippage: float
    total: float


def apply_slippage(
    raw_price: float,
    side: OrderSide,
    slippage_points_per_side: float,
) -> float:
    """Apply conservative one-side slippage to a raw execution price."""
    if slippage_points_per_side < 0:
        raise ValueError("slippage_points_per_side must be non-negative")
    if side == "BUY":
        return raw_price + slippage_points_per_side
    return raw_price - slippage_points_per_side


def calculate_order_cost(price: float, qty: int, cost_model: CostModel) -> OrderCost:
    """Calculate one-side commission, tax, and reported slippage cost."""
    if qty <= 0:
        raise ValueError("qty must be positive")

    fee = cost_model.commission_per_side * qty
    tax = price * cost_model.point_value * cost_model.tax_rate * qty
    slippage = cost_model.slippage_points_per_side * cost_model.point_value * qty
    return OrderCost(
        fee=fee,
        tax=tax,
        slippage=slippage,
        total=fee + tax + slippage,
    )
