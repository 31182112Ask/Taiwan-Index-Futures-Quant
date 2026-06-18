from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tifq.indicators import (
    append_basic_indicators,
    atr,
    ema,
    realized_volatility,
    session_vwap,
)


def bar_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    "2026-06-17 08:45:00",
                    "2026-06-17 08:50:00",
                    "2026-06-17 08:55:00",
                    "2026-06-18 08:45:00",
                    "2026-06-18 08:50:00",
                ]
            ).tz_localize("Asia/Taipei"),
            "open": [10.0, 12.0, 11.0, 20.0, 21.0],
            "high": [12.0, 13.0, 15.0, 22.0, 23.0],
            "low": [9.0, 10.0, 10.0, 19.0, 20.0],
            "close": [11.0, 12.0, 14.0, 21.0, 22.0],
            "volume": [10.0, 20.0, 30.0, 40.0, 50.0],
        },
        index=pd.Index(["a", "b", "c", "d", "e"], name="bar_id"),
    )


def test_ema_output_length_and_index_are_preserved() -> None:
    close = bar_frame()["close"]

    result = ema(close, span=3)

    assert len(result) == len(close)
    assert result.index.equals(close.index)


def test_ema_invalid_span_raises_value_error() -> None:
    with pytest.raises(ValueError, match="span"):
        ema(bar_frame()["close"], span=0)


def test_session_vwap_resets_each_trading_day_and_preserves_index() -> None:
    bars = bar_frame()

    result = session_vwap(bars)

    assert result.index.equals(bars.index)
    assert result.loc["a"] == 11
    assert result.loc["b"] == pytest.approx((11 * 10 + 12 * 20) / 30)
    assert result.loc["d"] == 21


def test_session_vwap_zero_cumulative_volume_returns_nan() -> None:
    bars = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-06-17 08:45:00", "2026-06-17 08:50:00"]),
            "close": [10.0, 11.0],
            "volume": [0.0, 5.0],
        },
        index=["first", "second"],
    )

    result = session_vwap(bars)

    assert np.isnan(result.loc["first"])
    assert result.loc["second"] == 11
    assert result.index.equals(bars.index)


def test_atr_true_range_and_rolling_mean_are_correct() -> None:
    bars = pd.DataFrame(
        {
            "high": [12.0, 15.0, 14.0],
            "low": [10.0, 11.0, 8.0],
            "close": [11.0, 12.0, 9.0],
        },
        index=["a", "b", "c"],
    )

    result = atr(bars, period=2)

    assert np.isnan(result.loc["a"])
    assert result.loc["b"] == pytest.approx((2 + 4) / 2)
    assert result.loc["c"] == pytest.approx((4 + 6) / 2)
    assert result.index.equals(bars.index)


def test_atr_invalid_period_raises_value_error() -> None:
    with pytest.raises(ValueError, match="period"):
        atr(bar_frame(), period=0)


def test_atr_does_not_use_future_data() -> None:
    bars = pd.DataFrame(
        {
            "high": [10.0, 12.0, 1000.0],
            "low": [9.0, 10.0, 1.0],
            "close": [9.5, 11.0, 500.0],
        }
    )
    truncated = bars.iloc[:2].copy()

    full_result = atr(bars, period=2)
    truncated_result = atr(truncated, period=2)

    assert full_result.iloc[1] == truncated_result.iloc[1]


def test_realized_volatility_length_nan_history_and_index_are_preserved() -> None:
    close = pd.Series([100.0, 101.0, 102.0, 103.0], index=["a", "b", "c", "d"])

    result = realized_volatility(close, window=3)

    assert len(result) == len(close)
    assert result.index.equals(close.index)
    assert result.iloc[:3].isna().all()
    assert not np.isnan(result.iloc[3])


def test_realized_volatility_invalid_window_raises_value_error() -> None:
    with pytest.raises(ValueError, match="window"):
        realized_volatility(bar_frame()["close"], window=1)


def test_append_basic_indicators_returns_new_frame_and_preserves_original() -> None:
    bars = bar_frame()
    original = bars.copy(deep=True)

    result = append_basic_indicators(
        bars,
        ema_fast=2,
        ema_slow=3,
        atr_period=2,
        volatility_window=2,
    )

    assert result is not bars
    pd.testing.assert_frame_equal(bars, original)
    assert {"ema_fast", "ema_slow", "vwap", "atr", "realized_volatility"}.issubset(
        result.columns
    )
    assert len(result) == len(bars)

