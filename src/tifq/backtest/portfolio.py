"""Portfolio accounting for conservative historical backtests."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

import pandas as pd

from tifq.backtest.cost import CostModel, OrderSide, apply_slippage, calculate_order_cost

PositionSide = Literal["LONG", "SHORT"]

TRADE_REQUIRED_COLUMNS: tuple[str, ...] = (
    "entry_time",
    "exit_time",
    "symbol",
    "side",
    "qty",
    "entry_price",
    "exit_price",
    "gross_pnl",
    "fee",
    "tax",
    "slippage",
    "net_pnl",
    "exit_reason",
)


@dataclass(frozen=True)
class OpenPosition:
    """Currently open simulated position."""

    entry_time: pd.Timestamp
    symbol: str
    side: PositionSide
    qty: int
    entry_price: float
    entry_fee: float
    entry_tax: float
    entry_slippage: float


@dataclass(frozen=True)
class Trade:
    """Closed trade record using the V1 required trade schema."""

    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    symbol: str
    side: PositionSide
    qty: int
    entry_price: float
    exit_price: float
    gross_pnl: float
    fee: float
    tax: float
    slippage: float
    net_pnl: float
    exit_reason: str


class Portfolio:
    """Single-position V1 portfolio ledger."""

    def __init__(self, initial_cash: float) -> None:
        if initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.open_position: OpenPosition | None = None
        self.trades: list[Trade] = []

    @property
    def current_position(self) -> int:
        """Return signed position quantity: long > 0, short < 0, flat = 0."""
        if self.open_position is None:
            return 0
        if self.open_position.side == "LONG":
            return self.open_position.qty
        return -self.open_position.qty

    def open(
        self,
        *,
        timestamp: pd.Timestamp,
        symbol: str,
        side: PositionSide,
        raw_price: float,
        qty: int,
        cost_model: CostModel,
    ) -> None:
        """Open one simulated position at a slippage-adjusted execution price."""
        if self.open_position is not None:
            raise ValueError("cannot open a new position while another position is open")
        if qty <= 0:
            raise ValueError("qty must be positive")

        order_side: OrderSide = "BUY" if side == "LONG" else "SELL"
        execution_price = apply_slippage(
            raw_price,
            order_side,
            cost_model.slippage_points_per_side,
        )
        cost = calculate_order_cost(execution_price, qty, cost_model)
        self.open_position = OpenPosition(
            entry_time=timestamp,
            symbol=symbol,
            side=side,
            qty=qty,
            entry_price=execution_price,
            entry_fee=cost.fee,
            entry_tax=cost.tax,
            entry_slippage=cost.slippage,
        )

    def close(
        self,
        *,
        timestamp: pd.Timestamp,
        raw_price: float,
        cost_model: CostModel,
        reason: str,
    ) -> Trade:
        """Close the current position and append a trade record."""
        if self.open_position is None:
            raise ValueError("cannot close without an open position")

        position = self.open_position
        order_side: OrderSide = "SELL" if position.side == "LONG" else "BUY"
        exit_price = apply_slippage(raw_price, order_side, cost_model.slippage_points_per_side)
        exit_cost = calculate_order_cost(exit_price, position.qty, cost_model)

        gross_pnl = _gross_pnl(position, exit_price, cost_model.point_value)
        fee = position.entry_fee + exit_cost.fee
        tax = position.entry_tax + exit_cost.tax
        slippage = position.entry_slippage + exit_cost.slippage
        net_pnl = gross_pnl - fee - tax
        trade = Trade(
            entry_time=position.entry_time,
            exit_time=timestamp,
            symbol=position.symbol,
            side=position.side,
            qty=position.qty,
            entry_price=position.entry_price,
            exit_price=exit_price,
            gross_pnl=gross_pnl,
            fee=fee,
            tax=tax,
            slippage=slippage,
            net_pnl=net_pnl,
            exit_reason=reason,
        )
        self.cash += net_pnl
        self.open_position = None
        self.trades.append(trade)
        return trade

    def mark_to_market(self, close_price: float, point_value: float) -> float:
        """Return cash plus unrealized PnL at the current close price."""
        if self.open_position is None:
            return self.cash
        return self.cash + _gross_pnl(self.open_position, close_price, point_value)

    def trades_frame(self) -> pd.DataFrame:
        """Return closed trades as a DataFrame with stable columns."""
        records = [asdict(trade) for trade in self.trades]
        return pd.DataFrame(records, columns=list(TRADE_REQUIRED_COLUMNS))


def _gross_pnl(position: OpenPosition, exit_price: float, point_value: float) -> float:
    if position.side == "LONG":
        return (exit_price - position.entry_price) * point_value * position.qty
    return (position.entry_price - exit_price) * point_value * position.qty
