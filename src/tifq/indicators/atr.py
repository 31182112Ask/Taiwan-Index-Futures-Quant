"""Average true range indicator."""

from __future__ import annotations

import pandas as pd


def atr(bars: pd.DataFrame, period: int) -> pd.Series:
    """Return V1 rolling-mean ATR using current and past bars only."""
    if period <= 0:
        raise ValueError(f"ATR period must be greater than 0; got: {period}")
    _require_columns(bars, ("high", "low", "close"))

    high = pd.to_numeric(bars["high"], errors="coerce")
    low = pd.to_numeric(bars["low"], errors="coerce")
    close = pd.to_numeric(bars["close"], errors="coerce")
    previous_close = close.shift(1)

    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    result = true_range.rolling(window=period, min_periods=period).mean()
    result.index = bars.index
    return result


def _require_columns(df: pd.DataFrame, columns: tuple[str, ...]) -> None:
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise ValueError(f"bars missing required columns: {', '.join(missing)}")

