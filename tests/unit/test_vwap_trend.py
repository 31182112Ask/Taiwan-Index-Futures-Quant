from __future__ import annotations

import pandas as pd
import pytest

from tifq.strategy import SIGNAL_REQUIRED_COLUMNS, VWAPTrendParams, VWAPTrendStrategy


def bars(rows: list[dict[str, object]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"]).dt.tz_localize("Asia/Taipei")
    return frame


def strategy(**overrides: object) -> VWAPTrendStrategy:
    params = {
        "atr_stop_mult": 1.5,
        "take_profit_r": 1.5,
        "min_atr_points": 1,
        "max_atr_points": 100,
        "max_trades_per_day": 3,
    }
    params.update(overrides)
    return VWAPTrendStrategy(VWAPTrendParams(**params))


def test_vwap_trend_emits_long_entry_with_risk_levels() -> None:
    input_bars = bars(
        [
            {
                "timestamp": "2026-06-17 08:55:00",
                "symbol": "TMF",
                "close": 99.0,
                "ema_fast": 100.0,
                "ema_slow": 98.0,
                "vwap": 99.0,
                "atr": 10.0,
            },
            {
                "timestamp": "2026-06-17 09:00:00",
                "symbol": "TMF",
                "close": 105.0,
                "ema_fast": 102.0,
                "ema_slow": 100.0,
                "vwap": 101.0,
                "atr": 10.0,
            },
        ]
    )

    signals = strategy().generate_signals(input_bars)

    assert tuple(signals.columns) == SIGNAL_REQUIRED_COLUMNS
    assert signals.loc[1, "side"] == "BUY"
    assert signals.loc[1, "target_position"] == 1
    assert signals.loc[1, "reason"] == "long_entry"
    assert signals.loc[1, "stop_loss"] == 90.0
    assert signals.loc[1, "take_profit"] == 127.5


def test_vwap_trend_emits_short_entry_with_risk_levels() -> None:
    input_bars = bars(
        [
            {
                "timestamp": "2026-06-17 08:55:00",
                "symbol": "TMF",
                "close": 105.0,
                "ema_fast": 104.0,
                "ema_slow": 106.0,
                "vwap": 105.0,
                "atr": 8.0,
            },
            {
                "timestamp": "2026-06-17 09:00:00",
                "symbol": "TMF",
                "close": 99.0,
                "ema_fast": 101.0,
                "ema_slow": 103.0,
                "vwap": 102.0,
                "atr": 8.0,
            },
        ]
    )

    signals = strategy().generate_signals(input_bars)

    assert signals.loc[1, "side"] == "SELL"
    assert signals.loc[1, "target_position"] == -1
    assert signals.loc[1, "reason"] == "short_entry"
    assert signals.loc[1, "stop_loss"] == 111.0
    assert signals.loc[1, "take_profit"] == 81.0


def test_vwap_trend_force_flattens_open_position() -> None:
    input_bars = bars(
        [
            {
                "timestamp": "2026-06-17 09:00:00",
                "symbol": "TMF",
                "close": 99.0,
                "ema_fast": 100.0,
                "ema_slow": 98.0,
                "vwap": 99.0,
                "atr": 10.0,
            },
            {
                "timestamp": "2026-06-17 09:05:00",
                "symbol": "TMF",
                "close": 105.0,
                "ema_fast": 102.0,
                "ema_slow": 100.0,
                "vwap": 101.0,
                "atr": 10.0,
            },
            {
                "timestamp": "2026-06-17 13:35:00",
                "symbol": "TMF",
                "close": 106.0,
                "ema_fast": 103.0,
                "ema_slow": 101.0,
                "vwap": 102.0,
                "atr": 10.0,
            },
        ]
    )

    signals = strategy().generate_signals(input_bars)

    assert signals.loc[2, "side"] == "FLAT"
    assert signals.loc[2, "target_position"] == 0
    assert signals.loc[2, "reason"] == "force_flatten"


def test_vwap_trend_exits_on_reverse_signal() -> None:
    input_bars = bars(
        [
            {
                "timestamp": "2026-06-17 09:00:00",
                "symbol": "TMF",
                "close": 99.0,
                "ema_fast": 100.0,
                "ema_slow": 98.0,
                "vwap": 99.0,
                "atr": 10.0,
            },
            {
                "timestamp": "2026-06-17 09:05:00",
                "symbol": "TMF",
                "close": 105.0,
                "ema_fast": 102.0,
                "ema_slow": 100.0,
                "vwap": 101.0,
                "atr": 10.0,
            },
            {
                "timestamp": "2026-06-17 09:10:00",
                "symbol": "TMF",
                "close": 98.0,
                "ema_fast": 100.0,
                "ema_slow": 102.0,
                "vwap": 101.0,
                "atr": 10.0,
            },
        ]
    )

    signals = strategy().generate_signals(input_bars)

    assert signals.loc[2, "side"] == "FLAT"
    assert signals.loc[2, "reason"] == "reverse_short"


def test_vwap_trend_respects_max_trades_per_day() -> None:
    input_bars = bars(
        [
            {
                "timestamp": "2026-06-17 08:55:00",
                "symbol": "TMF",
                "close": 99.0,
                "ema_fast": 100.0,
                "ema_slow": 98.0,
                "vwap": 99.0,
                "atr": 10.0,
            },
            {
                "timestamp": "2026-06-17 09:00:00",
                "symbol": "TMF",
                "close": 105.0,
                "ema_fast": 102.0,
                "ema_slow": 100.0,
                "vwap": 101.0,
                "atr": 10.0,
            },
        ]
    )

    signals = strategy(max_trades_per_day=0).generate_signals(input_bars)

    assert signals["side"].tolist() == ["HOLD", "HOLD"]


def test_vwap_trend_does_not_mutate_input_frame() -> None:
    input_bars = bars(
        [
            {
                "timestamp": "2026-06-17 08:55:00",
                "symbol": "TMF",
                "close": 99.0,
                "ema_fast": 100.0,
                "ema_slow": 98.0,
                "vwap": 99.0,
                "atr": 10.0,
            }
        ]
    )
    original = input_bars.copy(deep=True)

    strategy().generate_signals(input_bars)

    pd.testing.assert_frame_equal(input_bars, original)


def test_vwap_trend_rejects_missing_indicator_columns() -> None:
    input_bars = bars(
        [
            {
                "timestamp": "2026-06-17 08:55:00",
                "symbol": "TMF",
                "close": 99.0,
                "ema_fast": 100.0,
                "ema_slow": 98.0,
                "atr": 10.0,
            }
        ]
    )

    with pytest.raises(ValueError, match="missing required columns"):
        strategy().generate_signals(input_bars)

