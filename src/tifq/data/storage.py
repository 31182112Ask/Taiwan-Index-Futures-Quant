"""Parquet storage and V1 path helpers."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from tifq.data.schemas import V1_SYMBOL, V1_TIMEFRAMES


def write_parquet(df: pd.DataFrame, path: str | Path) -> None:
    """Write a DataFrame to Parquet, creating parent directories if needed."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path, index=False)


def read_parquet(path: str | Path) -> pd.DataFrame:
    """Read a Parquet file into a DataFrame."""
    return pd.read_parquet(Path(path))


def tick_path(processed_dir: Path, symbol: str, date: date) -> Path:
    """Return the V1 cleaned tick Parquet path for one symbol and date."""
    _validate_symbol(symbol)
    return processed_dir / "ticks" / symbol / f"{date.isoformat()}.parquet"


def bar_path(processed_dir: Path, symbol: str, timeframe: str, date: date) -> Path:
    """Return the V1 OHLCV bar Parquet path for one symbol, timeframe, and date."""
    _validate_symbol(symbol)
    if timeframe not in V1_TIMEFRAMES:
        allowed = ", ".join(sorted(V1_TIMEFRAMES))
        raise ValueError(f"V1 supports timeframes {{{allowed}}} only; got: {timeframe}")
    return processed_dir / "bars" / symbol / timeframe / f"{date.isoformat()}.parquet"


def _validate_symbol(symbol: str) -> None:
    if symbol != V1_SYMBOL:
        raise ValueError(f"V1 supports symbol {V1_SYMBOL} only; got: {symbol}")

