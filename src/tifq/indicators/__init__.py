"""Technical indicator functions."""

from __future__ import annotations

import pandas as pd

from tifq.indicators.atr import atr
from tifq.indicators.ema import ema
from tifq.indicators.volatility import realized_volatility
from tifq.indicators.vwap import session_vwap

__all__ = [
    "append_basic_indicators",
    "atr",
    "ema",
    "realized_volatility",
    "session_vwap",
]


def append_basic_indicators(
    bars: pd.DataFrame,
    *,
    ema_fast: int,
    ema_slow: int,
    atr_period: int,
    volatility_window: int,
) -> pd.DataFrame:
    """Append V1 indicator columns to a copy of a bar DataFrame."""
    result = bars.copy()
    if "contract_segment_id" not in result.columns:
        _append_one_segment(
            result,
            result.index,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            atr_period=atr_period,
            volatility_window=volatility_window,
            enforce_warmup=False,
        )
        return result

    for indices in result.groupby("contract_segment_id", sort=False).groups.values():
        _append_one_segment(
            result,
            pd.Index(indices),
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            atr_period=atr_period,
            volatility_window=volatility_window,
            enforce_warmup=True,
        )
    return result


def _append_one_segment(
    result: pd.DataFrame,
    indices: pd.Index,
    *,
    ema_fast: int,
    ema_slow: int,
    atr_period: int,
    volatility_window: int,
    enforce_warmup: bool,
) -> None:
    segment = result.loc[indices]
    fast = ema(segment["close"], ema_fast)
    slow = ema(segment["close"], ema_slow)
    if enforce_warmup:
        fast.iloc[: max(0, ema_fast - 1)] = float("nan")
        slow.iloc[: max(0, ema_slow - 1)] = float("nan")
    result.loc[indices, "ema_fast"] = fast
    result.loc[indices, "ema_slow"] = slow
    result.loc[indices, "vwap"] = session_vwap(segment)
    result.loc[indices, "atr"] = atr(segment, atr_period)
    result.loc[indices, "realized_volatility"] = realized_volatility(
        segment["close"], volatility_window
    )
