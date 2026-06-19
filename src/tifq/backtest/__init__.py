"""Conservative historical backtesting components."""

from tifq.backtest.contracts import ContractSelectionResult, select_contract_bars
from tifq.backtest.cost import CostModel, OrderCost, apply_slippage, calculate_order_cost
from tifq.backtest.engine import (
    BacktestEngine,
    BacktestPreflight,
    BacktestResult,
    build_data_fingerprint,
    discover_bar_files,
    load_configured_bars,
    prepare_backtest,
    run_backtest_from_config,
)
from tifq.backtest.metrics import calculate_metrics
from tifq.backtest.portfolio import Portfolio, Trade
from tifq.backtest.report import BacktestReportPaths, make_run_id, persist_backtest_result

__all__ = [
    "BacktestEngine",
    "BacktestPreflight",
    "BacktestResult",
    "BacktestReportPaths",
    "CostModel",
    "ContractSelectionResult",
    "OrderCost",
    "Portfolio",
    "Trade",
    "apply_slippage",
    "build_data_fingerprint",
    "calculate_order_cost",
    "calculate_metrics",
    "discover_bar_files",
    "load_configured_bars",
    "make_run_id",
    "persist_backtest_result",
    "prepare_backtest",
    "run_backtest_from_config",
    "select_contract_bars",
]
