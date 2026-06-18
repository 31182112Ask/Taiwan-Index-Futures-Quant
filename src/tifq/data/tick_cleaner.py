"""Tick cleaning utilities for normalized V1 TAIFEX data."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from tifq.data.schemas import TICK_REQUIRED_COLUMNS, V1_SYMBOL, validate_tick_frame

TAIPEI_TZ = "Asia/Taipei"


@dataclass(frozen=True)
class TickCleanResult:
    """Result of cleaning normalized tick rows."""

    ticks: pd.DataFrame
    input_row_count: int
    invalid_row_count: int


def clean_tick_frame(df: pd.DataFrame, *, symbol: str = V1_SYMBOL) -> TickCleanResult:
    """Clean normalized tick rows and return a validated V1 tick DataFrame.

    The input must already use the internal tick column names. This function does
    not infer raw TAIFEX column names; that belongs to the importer.
    """
    if symbol != V1_SYMBOL:
        raise ValueError(f"V1 supports symbol {V1_SYMBOL} only; got: {symbol}")

    missing_columns = [column for column in TICK_REQUIRED_COLUMNS if column not in df.columns]
    if missing_columns:
        missing = ", ".join(missing_columns)
        raise ValueError(f"normalized tick frame is missing required columns: {missing}")

    input_row_count = len(df)
    ticks = df.loc[:, list(TICK_REQUIRED_COLUMNS)].copy()
    ticks["symbol"] = ticks["symbol"].astype(str).str.strip()
    ticks["contract"] = ticks["contract"].astype(str).str.strip()
    ticks["source"] = ticks["source"].astype(str).str.strip()
    ticks["timestamp"] = _parse_taipei_timestamp(ticks["timestamp"])
    ticks["price"] = _parse_numeric_series(ticks["price"])
    ticks["volume"] = _parse_numeric_series(ticks["volume"])

    valid_mask = (
        (ticks["symbol"] == symbol)
        & ticks["timestamp"].notna()
        & ticks["price"].notna()
        & ticks["volume"].notna()
        & (ticks["price"] > 0)
        & (ticks["volume"] > 0)
    )
    cleaned = ticks.loc[valid_mask].copy()
    cleaned["volume"] = cleaned["volume"].astype("int64")
    cleaned = cleaned.sort_values("timestamp", kind="mergesort").reset_index(drop=True)

    invalid_row_count = input_row_count - len(cleaned)
    validate_tick_frame(cleaned, allow_empty=True)
    return TickCleanResult(
        ticks=cleaned,
        input_row_count=input_row_count,
        invalid_row_count=invalid_row_count,
    )


def _parse_taipei_timestamp(values: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce")
    if isinstance(parsed.dtype, pd.DatetimeTZDtype):
        return parsed.dt.tz_convert(TAIPEI_TZ)
    return parsed.dt.tz_localize(TAIPEI_TZ, nonexistent="NaT", ambiguous="NaT")


def _parse_numeric_series(values: pd.Series) -> pd.Series:
    as_text = values.astype(str).str.replace(",", "", regex=False).str.strip()
    return pd.to_numeric(as_text, errors="coerce")
