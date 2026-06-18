"""Exponential moving average indicator."""

from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    """Return an EMA that uses only current and past observations."""
    if span <= 0:
        raise ValueError(f"EMA span must be greater than 0; got: {span}")
    return series.ewm(span=span, adjust=False).mean()

