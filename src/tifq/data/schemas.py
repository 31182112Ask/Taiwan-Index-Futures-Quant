"""Internal DataFrame schemas for V1 market data."""

from __future__ import annotations

import pandas as pd
from pandas.api.types import is_bool_dtype, is_numeric_dtype

V1_SYMBOL = "TMF"
V1_TIMEFRAMES = frozenset({"1m", "5m"})

TICK_REQUIRED_COLUMNS: tuple[str, ...] = (
    "symbol",
    "contract",
    "timestamp",
    "price",
    "volume",
    "source",
)

BAR_REQUIRED_COLUMNS: tuple[str, ...] = (
    "symbol",
    "contract",
    "timeframe",
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
)

TICK_NUMERIC_COLUMNS: tuple[str, ...] = ("price", "volume")
BAR_NUMERIC_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")


class SchemaValidationError(ValueError):
    """Raised when a DataFrame does not match the internal V1 schema."""


def validate_tick_frame(df: pd.DataFrame, *, allow_empty: bool = False) -> None:
    """Validate a cleaned tick DataFrame against the V1 internal schema."""
    _validate_required_columns(df, TICK_REQUIRED_COLUMNS, "tick")
    _validate_not_empty(df, "tick", allow_empty=allow_empty)
    if df.empty:
        return

    _validate_symbol_column(df)
    _validate_numeric_columns(df, TICK_NUMERIC_COLUMNS, "tick")


def validate_bar_frame(df: pd.DataFrame, *, allow_empty: bool = False) -> None:
    """Validate an OHLCV bar DataFrame against the V1 internal schema."""
    _validate_required_columns(df, BAR_REQUIRED_COLUMNS, "bar")
    _validate_not_empty(df, "bar", allow_empty=allow_empty)
    if df.empty:
        return

    _validate_symbol_column(df)
    _validate_timeframe_column(df)
    _validate_numeric_columns(df, BAR_NUMERIC_COLUMNS, "bar")


def _validate_required_columns(
    df: pd.DataFrame,
    required_columns: tuple[str, ...],
    frame_name: str,
) -> None:
    missing_columns = [column for column in required_columns if column not in df.columns]
    if missing_columns:
        missing = ", ".join(missing_columns)
        raise SchemaValidationError(f"{frame_name} frame is missing required columns: {missing}")


def _validate_not_empty(df: pd.DataFrame, frame_name: str, *, allow_empty: bool) -> None:
    if df.empty and not allow_empty:
        raise SchemaValidationError(f"{frame_name} frame must not be empty")


def _validate_symbol_column(df: pd.DataFrame) -> None:
    invalid_symbols = sorted(
        symbol for symbol in set(df["symbol"].astype(str)) if symbol != V1_SYMBOL
    )
    if invalid_symbols:
        invalid = ", ".join(invalid_symbols)
        raise SchemaValidationError(f"V1 supports symbol {V1_SYMBOL} only; got: {invalid}")


def _validate_timeframe_column(df: pd.DataFrame) -> None:
    invalid_timeframes = sorted(
        timeframe
        for timeframe in set(df["timeframe"].astype(str))
        if timeframe not in V1_TIMEFRAMES
    )
    if invalid_timeframes:
        invalid = ", ".join(invalid_timeframes)
        allowed = ", ".join(sorted(V1_TIMEFRAMES))
        raise SchemaValidationError(f"V1 supports timeframes {{{allowed}}} only; got: {invalid}")


def _validate_numeric_columns(
    df: pd.DataFrame,
    numeric_columns: tuple[str, ...],
    frame_name: str,
) -> None:
    non_numeric_columns = [
        column
        for column in numeric_columns
        if not is_numeric_dtype(df[column]) or is_bool_dtype(df[column])
    ]
    if non_numeric_columns:
        columns = ", ".join(non_numeric_columns)
        raise SchemaValidationError(f"{frame_name} frame columns must be numeric: {columns}")

