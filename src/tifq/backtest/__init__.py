"""Conservative historical backtesting components."""

from tifq.backtest.cost import CostModel, OrderCost, apply_slippage, calculate_order_cost
from tifq.backtest.engine import (
    BacktestEngine,
    BacktestResult,
    discover_bar_files,
    load_configured_bars,
    run_backtest_from_config,
)
from tifq.backtest.metrics import calculate_metrics
from tifq.backtest.portfolio import Portfolio, Trade
from tifq.backtest.report import BacktestReportPaths, make_run_id, persist_backtest_result

__all__ = [
    "BacktestEngine",
    "BacktestResult",
    "BacktestReportPaths",
    "CostModel",
    "OrderCost",
    "Portfolio",
    "Trade",
    "apply_slippage",
    "calculate_order_cost",
    "calculate_metrics",
    "discover_bar_files",
    "load_configured_bars",
    "make_run_id",
    "persist_backtest_result",
    "run_backtest_from_config",
]
