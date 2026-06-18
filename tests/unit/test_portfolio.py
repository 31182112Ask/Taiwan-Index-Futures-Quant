from __future__ import annotations

import pandas as pd

from tifq.backtest import CostModel, Portfolio
from tifq.backtest.portfolio import TRADE_REQUIRED_COLUMNS


def test_portfolio_long_trade_pnl_and_flattened_position() -> None:
    portfolio = Portfolio(initial_cash=100_000)
    cost_model = CostModel(point_value=10, commission_per_side=5, slippage_points_per_side=1)

    portfolio.open(
        timestamp=pd.Timestamp("2026-06-17 09:05:00", tz="Asia/Taipei"),
        symbol="TMF",
        side="LONG",
        raw_price=100.0,
        qty=1,
        cost_model=cost_model,
    )
    trade = portfolio.close(
        timestamp=pd.Timestamp("2026-06-17 09:10:00", tz="Asia/Taipei"),
        raw_price=110.0,
        cost_model=cost_model,
        reason="take_profit",
    )

    assert trade.entry_price == 101.0
    assert trade.exit_price == 109.0
    assert trade.gross_pnl == 80.0
    assert trade.fee == 10.0
    assert trade.slippage == 20.0
    assert trade.net_pnl == 70.0
    assert portfolio.current_position == 0
    assert portfolio.cash == 100_070.0


def test_portfolio_short_trade_pnl_and_complete_trade_record() -> None:
    portfolio = Portfolio(initial_cash=100_000)
    cost_model = CostModel(point_value=10, commission_per_side=5, slippage_points_per_side=1)

    portfolio.open(
        timestamp=pd.Timestamp("2026-06-17 09:05:00", tz="Asia/Taipei"),
        symbol="TMF",
        side="SHORT",
        raw_price=100.0,
        qty=1,
        cost_model=cost_model,
    )
    trade = portfolio.close(
        timestamp=pd.Timestamp("2026-06-17 09:10:00", tz="Asia/Taipei"),
        raw_price=90.0,
        cost_model=cost_model,
        reason="take_profit",
    )

    assert trade.entry_price == 99.0
    assert trade.exit_price == 91.0
    assert trade.gross_pnl == 80.0
    assert trade.net_pnl == 70.0
    assert portfolio.current_position == 0
    assert tuple(portfolio.trades_frame().columns) == TRADE_REQUIRED_COLUMNS
