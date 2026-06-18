"""Tick-to-OHLCV resampling for V1."""

from __future__ import annotations

from datetime import time
from typing import cast

import pandas as pd

from tifq.data.schemas import V1_SYMBOL, V1_TIMEFRAMES, validate_bar_frame, validate_tick_frame

TAIPEI_TZ = "Asia/Taipei"
DAY_SESSION_START = time(8, 45)
DAY_SESSION_END = time(13, 45)

_TIMEFRAME_RULES = {
    "1m": "1min",
    "5m": "5min",
}


def resample_ticks_to_bars(
    ticks: pd.DataFrame,
    *,
    timeframe: str,
    symbol: str = V1_SYMBOL,
) -> pd.DataFrame:
    """Convert cleaned ticks into V1 OHLCV bars without crossing trading days."""
    _validate_symbol(symbol)
    _validate_timeframe(timeframe)
    validate_tick_frame(ticks)

    prepared_ticks = _prepare_ticks(ticks, symbol)
    if prepared_ticks.empty:
        return _empty_bar_frame()

    groups = prepared_ticks.groupby(
        ["symbol", "contract", "trading_date"],
        sort=True,
        group_keys=False,
    )
    bars = [
        _resample_one_group(group, timeframe=timeframe)
        for _, group in groups
        if not group.empty
    ]
    if not bars:
        return _empty_bar_frame()

    result = pd.concat(bars, ignore_index=True)
    result = result.sort_values(["timestamp", "symbol", "contract"], kind="mergesort")
    result = result.reset_index(drop=True)
    validate_bar_frame(result)
    return result


def _prepare_ticks(ticks: pd.DataFrame, symbol: str) -> pd.DataFrame:
    prepared = ticks.copy()
    prepared = prepared.loc[prepared["symbol"].astype(str) == symbol].copy()
    prepared["timestamp"] = _ensure_taipei_timestamp(prepared["timestamp"])
    prepared = prepared.dropna(subset=["timestamp"])
    prepared = prepared.loc[_is_day_session(prepared["timestamp"])].copy()
    prepared["trading_date"] = prepared["timestamp"].dt.date
    return prepared.sort_values("timestamp", kind="mergesort").reset_index(drop=True)


def _resample_one_group(group: pd.DataFrame, *, timeframe: str) -> pd.DataFrame:
    rule = _TIMEFRAME_RULES[timeframe]
    ordered = group.sort_values("timestamp", kind="mergesort").set_index("timestamp")
    ohlc = ordered["price"].resample(rule, label="left", closed="left").ohlc()
    volume = ordered["volume"].resample(rule, label="left", closed="left").sum()
    bars = ohlc.join(volume.rename("volume"))
    bars = bars.dropna(subset=["open"])
    if bars.empty:
        return _empty_bar_frame()

    bars = bars.reset_index()
    bars.insert(0, "timeframe", timeframe)
    bars.insert(0, "contract", str(group["contract"].iloc[0]))
    bars.insert(0, "symbol", str(group["symbol"].iloc[0]))
    return bars.loc[
        :,
        ["symbol", "contract", "timeframe", "timestamp", "open", "high", "low", "close", "volume"],
    ]


def _ensure_taipei_timestamp(values: pd.Series) -> pd.Series:
    timestamp = pd.to_datetime(values, errors="coerce")
    if isinstance(timestamp.dtype, pd.DatetimeTZDtype):
        return timestamp.dt.tz_convert(TAIPEI_TZ)
    return timestamp.dt.tz_localize(TAIPEI_TZ, nonexistent="NaT", ambiguous="NaT")


def _is_day_session(timestamps: pd.Series) -> pd.Series:
    timestamp_time = timestamps.dt.time
    mask = (timestamp_time >= DAY_SESSION_START) & (timestamp_time <= DAY_SESSION_END)
    return cast(pd.Series, mask)


def _empty_bar_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "symbol",
            "contract",
            "timeframe",
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    )


def _validate_symbol(symbol: str) -> None:
    if symbol != V1_SYMBOL:
        raise ValueError(f"V1 supports symbol {V1_SYMBOL} only; got: {symbol}")


def _validate_timeframe(timeframe: str) -> None:
    if timeframe not in V1_TIMEFRAMES:
        allowed = ", ".join(sorted(V1_TIMEFRAMES))
        raise ValueError(f"V1 supports timeframes {{{allowed}}} only; got: {timeframe}")
