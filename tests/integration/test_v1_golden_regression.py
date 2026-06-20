"""Frozen V1 execution semantics protected across application-boundary extraction."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from tifq.backtest import BacktestEngine, CostModel


def test_v1_next_bar_open_golden_regression() -> None:
    golden_path = Path(__file__).parents[1] / "fixtures" / "v1_golden" / "backtest.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    timestamps = pd.to_datetime(
        ["2026-06-17 09:00", "2026-06-17 09:05", "2026-06-17 09:10"]
    ).tz_localize("Asia/Taipei")
    bars = pd.DataFrame(
        {
            "timestamp": timestamps,
            "symbol": "TMF",
            "contract": "202606",
            "contract_segment_id": "segment_001",
            "timeframe": "5m",
            "open": [100.0, 101.0, 105.0],
            "high": [101.0, 103.0, 107.0],
            "low": [99.0, 100.0, 104.0],
            "close": [100.0, 102.0, 106.0],
            "volume": [10, 11, 12],
        }
    )
    signals = pd.DataFrame(
        {
            "timestamp": timestamps,
            "symbol": "TMF",
            "contract": "202606",
            "contract_segment_id": "segment_001",
            "side": golden["signal_sides"],
            "target_position": [1, 0, 0],
            "reason": ["long_entry", "force_flatten", "hold"],
            "stop_loss": None,
            "take_profit": None,
        }
    )
    result = BacktestEngine(
        initial_cash=100_000,
        cost_model=CostModel(
            point_value=10, commission_per_side=5, slippage_points_per_side=1
        ),
    ).run(bars, signals)
    trade = result.trades.iloc[0]
    expected = golden["trades"][0]

    assert trade["entry_time"].isoformat() == expected["entry_time"]
    assert trade["exit_time"].isoformat() == expected["exit_time"]
    for field in ("entry_price", "exit_price", "fee", "tax", "slippage", "net_pnl"):
        assert float(trade[field]) == expected[field]
    assert trade["exit_reason"] == expected["exit_reason"]
    for field, value in golden["metrics"].items():
        assert result.metrics[field] == value
