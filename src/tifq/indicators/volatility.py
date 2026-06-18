"""Realized volatility indicator."""

from __future__ import annotations

import numpy as np
import pandas as pd


def realized_volatility(close: pd.Series, window: int) -> pd.Series:
    """Return rolling standard deviation of log returns, not annualized."""
    if window <= 1:
        raise ValueError(f"realized volatility window must be greater than 1; got: {window}")

    numeric_close = pd.to_numeric(close, errors="coerce")
    log_close = pd.Series(np.log(numeric_close.to_numpy()), index=close.index)
    log_returns = log_close.diff()
    result = log_returns.rolling(window=window, min_periods=window).std()
    result.index = close.index
    return result
