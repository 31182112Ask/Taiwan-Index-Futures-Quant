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
    result["ema_fast"] = ema(result["close"], ema_fast)
    result["ema_slow"] = ema(result["close"], ema_slow)
    result["vwap"] = session_vwap(result)
    result["atr"] = atr(result, atr_period)
    result["realized_volatility"] = realized_volatility(result["close"], volatility_window)
    return result

