from __future__ import annotations

import pandas as pd
import pytest

from tifq.backtest import calculate_metrics


def test_calculate_metrics_includes_required_performance_statistics() -> None:
    trades = pd.DataFrame(
        {
            "net_pnl": [100.0, -25.0, 50.0, -75.0],
            "fee": [10.0, 10.0, 10.0, 10.0],
            "tax": [1.0, 1.0, 1.0, 1.0],
            "slippage": [20.0, 20.0, 20.0, 20.0],
        }
    )
    equity_curve = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    "2026-06-17 09:00:00",
                    "2026-06-17 09:05:00",
                    "2026-06-17 09:10:00",
                    "2026-06-17 09:15:00",
                ]
            ),
            "equity": [100_000.0, 100_100.0, 99_900.0, 100_050.0],
        }
    )

    metrics = calculate_metrics(100_000.0, trades, equity_curve)

    assert metrics["initial_cash"] == 100_000.0
    assert metrics["final_equity"] == 100_050.0
    assert metrics["net_pnl"] == 50.0
    assert metrics["return_pct"] == pytest.approx(0.0005)
    assert metrics["max_drawdown"] == 200.0
    assert metrics["max_drawdown_pct"] == pytest.approx(200.0 / 100_100.0)
    assert metrics["trade_count"] == 4
    assert metrics["win_rate"] == 0.5
    assert metrics["avg_win"] == 75.0
    assert metrics["avg_loss"] == -50.0
    assert metrics["profit_factor"] == 1.5
    assert metrics["expectancy"] == 12.5
    assert metrics["largest_win"] == 100.0
    assert metrics["largest_loss"] == -75.0
    assert metrics["total_fee"] == 40.0
    assert metrics["total_tax"] == 4.0
    assert metrics["total_slippage"] == 80.0


def test_calculate_metrics_handles_no_trades() -> None:
    metrics = calculate_metrics(
        100_000.0,
        pd.DataFrame(columns=["net_pnl", "fee", "tax", "slippage"]),
        pd.DataFrame({"equity": [100_000.0]}),
    )

    assert metrics["trade_count"] == 0
    assert metrics["win_rate"] == 0.0
    assert metrics["avg_win"] == 0.0
    assert metrics["avg_loss"] == 0.0
    assert metrics["profit_factor"] == 0.0
    assert metrics["expectancy"] == 0.0
    assert metrics["max_drawdown"] == 0.0
