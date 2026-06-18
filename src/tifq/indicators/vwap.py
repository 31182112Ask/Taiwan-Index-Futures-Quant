"""Session VWAP indicator."""

from __future__ import annotations

import pandas as pd


def session_vwap(bars: pd.DataFrame) -> pd.Series:
    """Return close-volume VWAP that resets on each trading day."""
    _require_columns(bars, ("timestamp", "close", "volume"))

    timestamp = pd.to_datetime(bars["timestamp"], errors="coerce")
    trading_date = timestamp.dt.date
    close = pd.to_numeric(bars["close"], errors="coerce")
    volume = pd.to_numeric(bars["volume"], errors="coerce")

    price_volume = close * volume
    cumulative_price_volume = price_volume.groupby(trading_date, sort=False).cumsum()
    cumulative_volume = volume.groupby(trading_date, sort=False).cumsum()

    vwap = cumulative_price_volume / cumulative_volume
    vwap = vwap.mask(cumulative_volume == 0)
    vwap.index = bars.index
    return vwap


def _require_columns(df: pd.DataFrame, columns: tuple[str, ...]) -> None:
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise ValueError(f"bars missing required columns: {', '.join(missing)}")

