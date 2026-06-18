"""Backtest performance metrics for V1 reports."""

from __future__ import annotations

import pandas as pd

MetricValue = float | int


def calculate_metrics(
    initial_cash: float,
    trades: pd.DataFrame,
    equity_curve: pd.DataFrame,
) -> dict[str, MetricValue]:
    """Calculate required V1 backtest metrics from trades and equity."""
    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")

    final_equity = _final_equity(initial_cash, equity_curve)
    net_pnl = final_equity - initial_cash
    trade_count = len(trades)
    net_pnls = _net_pnls(trades)
    wins = net_pnls[net_pnls > 0]
    losses = net_pnls[net_pnls < 0]
    total_fee = _sum_column(trades, "fee")
    total_tax = _sum_column(trades, "tax")
    total_slippage = _sum_column(trades, "slippage")
    max_drawdown, max_drawdown_pct = _drawdown(equity_curve)

    return {
        "initial_cash": float(initial_cash),
        "final_equity": final_equity,
        "net_pnl": net_pnl,
        "return_pct": net_pnl / initial_cash,
        "max_drawdown": max_drawdown,
        "max_drawdown_pct": max_drawdown_pct,
        "trade_count": trade_count,
        "win_rate": _win_rate(trade_count, wins),
        "avg_win": _mean_or_zero(wins),
        "avg_loss": _mean_or_zero(losses),
        "profit_factor": _profit_factor(wins, losses),
        "expectancy": _mean_or_zero(net_pnls),
        "largest_win": _max_or_zero(wins),
        "largest_loss": _min_or_zero(losses),
        "total_fee": total_fee,
        "total_tax": total_tax,
        "total_slippage": total_slippage,
    }


def _final_equity(initial_cash: float, equity_curve: pd.DataFrame) -> float:
    if equity_curve.empty:
        return float(initial_cash)
    if "equity" not in equity_curve.columns:
        raise ValueError("equity_curve must contain an equity column")
    return float(equity_curve.iloc[-1]["equity"])


def _drawdown(equity_curve: pd.DataFrame) -> tuple[float, float]:
    if equity_curve.empty:
        return 0.0, 0.0
    if "equity" not in equity_curve.columns:
        raise ValueError("equity_curve must contain an equity column")

    equity = pd.to_numeric(equity_curve["equity"], errors="raise").astype(float)
    running_peak = equity.cummax()
    drawdown = equity - running_peak
    drawdown_pct = drawdown / running_peak.where(running_peak != 0)
    return abs(float(drawdown.min())), abs(float(drawdown_pct.min()))


def _net_pnls(trades: pd.DataFrame) -> pd.Series:
    if trades.empty:
        return pd.Series(dtype=float)
    if "net_pnl" not in trades.columns:
        raise ValueError("trades must contain a net_pnl column")
    return pd.to_numeric(trades["net_pnl"], errors="raise").astype(float)


def _sum_column(trades: pd.DataFrame, column: str) -> float:
    if trades.empty:
        return 0.0
    if column not in trades.columns:
        raise ValueError(f"trades must contain a {column} column")
    return float(pd.to_numeric(trades[column], errors="raise").sum())


def _win_rate(trade_count: int, wins: pd.Series) -> float:
    if trade_count == 0:
        return 0.0
    return float(len(wins) / trade_count)


def _mean_or_zero(values: pd.Series) -> float:
    if values.empty:
        return 0.0
    return float(values.mean())


def _max_or_zero(values: pd.Series) -> float:
    if values.empty:
        return 0.0
    return float(values.max())


def _min_or_zero(values: pd.Series) -> float:
    if values.empty:
        return 0.0
    return float(values.min())


def _profit_factor(wins: pd.Series, losses: pd.Series) -> float:
    gross_win = float(wins.sum()) if not wins.empty else 0.0
    gross_loss = abs(float(losses.sum())) if not losses.empty else 0.0
    if gross_loss == 0:
        return 0.0
    return gross_win / gross_loss
