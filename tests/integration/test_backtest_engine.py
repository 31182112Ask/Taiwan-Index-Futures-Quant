from __future__ import annotations

import pandas as pd
import pytest

from tifq.backtest import BacktestEngine, CostModel


def bar_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"]).dt.tz_localize("Asia/Taipei")
    if "contract_segment_id" not in frame:
        frame["contract_segment_id"] = "segment_001"
    return frame


def signal_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"]).dt.tz_localize("Asia/Taipei")
    if "contract" not in frame:
        frame["contract"] = "202606"
    if "contract_segment_id" not in frame:
        frame["contract_segment_id"] = "segment_001"
    return frame


def segment_bars() -> pd.DataFrame:
    return bar_frame(
        [
            {
                "timestamp": "2026-06-17 13:25:00",
                "symbol": "TMF",
                "contract": "202606",
                "contract_segment_id": "segment_001",
                "timeframe": "5m",
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 10,
            },
            {
                "timestamp": "2026-06-17 13:30:00",
                "symbol": "TMF",
                "contract": "202606",
                "contract_segment_id": "segment_001",
                "timeframe": "5m",
                "open": 102.0,
                "high": 104.0,
                "low": 101.0,
                "close": 103.0,
                "volume": 10,
            },
            {
                "timestamp": "2026-06-18 08:45:00",
                "symbol": "TMF",
                "contract": "202607",
                "contract_segment_id": "segment_002",
                "timeframe": "5m",
                "open": 500.0,
                "high": 502.0,
                "low": 499.0,
                "close": 501.0,
                "volume": 10,
            },
            {
                "timestamp": "2026-06-18 08:50:00",
                "symbol": "TMF",
                "contract": "202607",
                "contract_segment_id": "segment_002",
                "timeframe": "5m",
                "open": 502.0,
                "high": 504.0,
                "low": 501.0,
                "close": 503.0,
                "volume": 10,
            },
        ]
    )


def segment_signals() -> pd.DataFrame:
    bars = segment_bars()
    return signal_frame(
        [
            {
                "timestamp": bars.loc[0, "timestamp"].tz_localize(None),
                "symbol": "TMF",
                "contract": "202606",
                "contract_segment_id": "segment_001",
                "side": "BUY",
                "target_position": 1,
                "reason": "long_entry",
                "stop_loss": None,
                "take_profit": None,
            },
            {
                "timestamp": bars.loc[1, "timestamp"].tz_localize(None),
                "symbol": "TMF",
                "contract": "202606",
                "contract_segment_id": "segment_001",
                "side": "BUY",
                "target_position": 1,
                "reason": "late_entry",
                "stop_loss": None,
                "take_profit": None,
            },
            {
                "timestamp": bars.loc[2, "timestamp"].tz_localize(None),
                "symbol": "TMF",
                "contract": "202607",
                "contract_segment_id": "segment_002",
                "side": "HOLD",
                "target_position": 0,
                "reason": "hold",
                "stop_loss": None,
                "take_profit": None,
            },
            {
                "timestamp": bars.loc[3, "timestamp"].tz_localize(None),
                "symbol": "TMF",
                "contract": "202607",
                "contract_segment_id": "segment_002",
                "side": "HOLD",
                "target_position": 0,
                "reason": "hold",
                "stop_loss": None,
                "take_profit": None,
            },
        ]
    )


def base_bars() -> pd.DataFrame:
    return bar_frame(
        [
            {
                "timestamp": "2026-06-17 09:00:00",
                "symbol": "TMF",
                "contract": "202606",
                "timeframe": "5m",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 10,
            },
            {
                "timestamp": "2026-06-17 09:05:00",
                "symbol": "TMF",
                "contract": "202606",
                "timeframe": "5m",
                "open": 101.0,
                "high": 103.0,
                "low": 100.0,
                "close": 102.0,
                "volume": 11,
            },
            {
                "timestamp": "2026-06-17 09:10:00",
                "symbol": "TMF",
                "contract": "202606",
                "timeframe": "5m",
                "open": 105.0,
                "high": 107.0,
                "low": 104.0,
                "close": 106.0,
                "volume": 12,
            },
        ]
    )


def test_signal_on_bar_n_executes_at_bar_n_plus_one_open() -> None:
    signals = signal_frame(
        [
            {
                "timestamp": "2026-06-17 09:00:00",
                "symbol": "TMF",
                "side": "BUY",
                "target_position": 1,
                "reason": "long_entry",
                "stop_loss": None,
                "take_profit": None,
            },
            {
                "timestamp": "2026-06-17 09:05:00",
                "symbol": "TMF",
                "side": "FLAT",
                "target_position": 0,
                "reason": "force_flatten",
                "stop_loss": None,
                "take_profit": None,
            },
            {
                "timestamp": "2026-06-17 09:10:00",
                "symbol": "TMF",
                "side": "HOLD",
                "target_position": 0,
                "reason": "hold",
                "stop_loss": None,
                "take_profit": None,
            },
        ]
    )
    engine = BacktestEngine(
        initial_cash=100_000,
        cost_model=CostModel(point_value=10, commission_per_side=5, slippage_points_per_side=1),
    )

    result = engine.run(base_bars(), signals)
    trade = result.trades.iloc[0]

    assert trade["entry_time"] == pd.Timestamp("2026-06-17 09:05:00", tz="Asia/Taipei")
    assert trade["entry_price"] == 102.0
    assert trade["exit_time"] == pd.Timestamp("2026-06-17 09:10:00", tz="Asia/Taipei")
    assert trade["exit_price"] == 104.0
    assert trade["exit_reason"] == "force_flatten"
    assert trade["fee"] == 10.0
    assert trade["slippage"] == 20.0
    assert trade["net_pnl"] == 10.0


def test_backtest_result_includes_equity_curve_trades_and_metrics() -> None:
    signals = signal_frame(
        [
            {
                "timestamp": "2026-06-17 09:00:00",
                "symbol": "TMF",
                "side": "BUY",
                "target_position": 1,
                "reason": "long_entry",
                "stop_loss": None,
                "take_profit": None,
            },
            {
                "timestamp": "2026-06-17 09:05:00",
                "symbol": "TMF",
                "side": "HOLD",
                "target_position": 1,
                "reason": "hold",
                "stop_loss": None,
                "take_profit": None,
            },
            {
                "timestamp": "2026-06-17 09:10:00",
                "symbol": "TMF",
                "side": "HOLD",
                "target_position": 1,
                "reason": "hold",
                "stop_loss": None,
                "take_profit": None,
            },
        ]
    )
    engine = BacktestEngine(
        initial_cash=100_000,
        cost_model=CostModel(
            point_value=10,
            commission_per_side=5,
            tax_rate=0.00002,
            slippage_points_per_side=1,
        ),
    )

    result = engine.run(base_bars(), signals)

    assert len(result.equity_curve) == 3
    assert result.trades.iloc[0]["exit_reason"] == "end_of_data"
    assert result.metrics["trade_count"] == 1
    assert result.metrics["total_fee"] == 10.0
    assert result.metrics["total_tax"] > 0
    assert result.metrics["total_slippage"] == 20.0
    assert result.metrics["final_equity"] == pytest.approx(result.equity_curve.iloc[-1]["equity"])


def test_engine_respects_max_trades_per_day() -> None:
    bars = bar_frame(
        [
            {
                "timestamp": f"2026-06-17 09:{minute:02d}:00",
                "symbol": "TMF",
                "contract": "202606",
                "timeframe": "5m",
                "open": 100.0 + index,
                "high": 102.0 + index,
                "low": 99.0 + index,
                "close": 101.0 + index,
                "volume": 10 + index,
            }
            for index, minute in enumerate([0, 5, 10, 15, 20])
        ]
    )
    signals = signal_frame(
        [
            {
                "timestamp": "2026-06-17 09:00:00",
                "symbol": "TMF",
                "side": "BUY",
                "target_position": 1,
                "reason": "entry_one",
                "stop_loss": None,
                "take_profit": None,
            },
            {
                "timestamp": "2026-06-17 09:05:00",
                "symbol": "TMF",
                "side": "FLAT",
                "target_position": 0,
                "reason": "exit_one",
                "stop_loss": None,
                "take_profit": None,
            },
            {
                "timestamp": "2026-06-17 09:10:00",
                "symbol": "TMF",
                "side": "BUY",
                "target_position": 1,
                "reason": "entry_two",
                "stop_loss": None,
                "take_profit": None,
            },
            {
                "timestamp": "2026-06-17 09:15:00",
                "symbol": "TMF",
                "side": "FLAT",
                "target_position": 0,
                "reason": "exit_two",
                "stop_loss": None,
                "take_profit": None,
            },
            {
                "timestamp": "2026-06-17 09:20:00",
                "symbol": "TMF",
                "side": "HOLD",
                "target_position": 0,
                "reason": "hold",
                "stop_loss": None,
                "take_profit": None,
            },
        ]
    )
    engine = BacktestEngine(
        initial_cash=100_000,
        cost_model=CostModel(point_value=10),
        max_trades_per_day=1,
    )

    result = engine.run(bars, signals)

    assert result.metrics["trade_count"] == 1
    assert result.trades["exit_reason"].tolist() == ["exit_one"]


def test_old_segment_entry_signal_is_not_executed_on_new_contract() -> None:
    result = BacktestEngine(initial_cash=100_000, cost_model=CostModel(point_value=10)).run(
        segment_bars(), segment_signals()
    )

    assert len(result.trades) == 1
    assert result.trades.iloc[0]["entry_price"] == 102.0
    assert not (result.trades["entry_price"] == 500.0).any()


def test_open_position_is_closed_before_contract_segment_changes() -> None:
    result = BacktestEngine(initial_cash=100_000, cost_model=CostModel(point_value=10)).run(
        segment_bars(), segment_signals()
    )

    trade = result.trades.iloc[0]
    assert trade["exit_time"] == pd.Timestamp("2026-06-17 13:30:00", tz="Asia/Taipei")
    assert trade["exit_price"] == 103.0


def test_equity_does_not_mark_old_position_with_new_contract_price() -> None:
    result = BacktestEngine(initial_cash=100_000, cost_model=CostModel(point_value=10)).run(
        segment_bars(), segment_signals()
    )

    assert result.equity_curve.loc[1, "equity"] == result.equity_curve.loc[2, "equity"]


def test_bar_signal_contract_and_segment_mismatch_is_rejected() -> None:
    signals = segment_signals()
    signals.loc[1, "contract"] = "202607"

    with pytest.raises(ValueError, match="contract must align exactly"):
        BacktestEngine(initial_cash=100_000, cost_model=CostModel(point_value=10)).run(
            segment_bars(), signals
        )


def test_contract_roll_trade_has_explicit_exit_reason() -> None:
    result = BacktestEngine(
        initial_cash=100_000,
        cost_model=CostModel(
            point_value=10,
            commission_per_side=5,
            tax_rate=0.00002,
            slippage_points_per_side=1,
        ),
    ).run(segment_bars(), segment_signals())

    trade = result.trades.iloc[0]
    assert trade["exit_reason"] == "contract_roll"
    assert trade["fee"] == 10.0
    assert trade["tax"] > 0
    assert trade["slippage"] == 20.0
