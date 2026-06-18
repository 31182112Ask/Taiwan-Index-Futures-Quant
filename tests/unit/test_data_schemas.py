from __future__ import annotations

import pandas as pd
import pytest

from tifq.data.schemas import (
    BAR_REQUIRED_COLUMNS,
    TICK_REQUIRED_COLUMNS,
    SchemaValidationError,
    validate_bar_frame,
    validate_tick_frame,
)


def valid_tick_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["TMF", "TMF"],
            "contract": ["202606", "202606"],
            "timestamp": pd.to_datetime(["2026-06-17 08:45:00", "2026-06-17 08:45:01"]),
            "price": [22000.0, 22001.0],
            "volume": [1, 2],
            "source": ["unit-test", "unit-test"],
        }
    )


def valid_bar_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["TMF"],
            "contract": ["202606"],
            "timeframe": ["5m"],
            "timestamp": pd.to_datetime(["2026-06-17 08:45:00"]),
            "open": [22000.0],
            "high": [22010.0],
            "low": [21990.0],
            "close": [22005.0],
            "volume": [10],
        }
    )


def test_tick_required_columns_are_stable() -> None:
    assert TICK_REQUIRED_COLUMNS == (
        "symbol",
        "contract",
        "timestamp",
        "price",
        "volume",
        "source",
    )


def test_bar_required_columns_are_stable() -> None:
    assert BAR_REQUIRED_COLUMNS == (
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


def test_validate_tick_frame_accepts_valid_frame() -> None:
    validate_tick_frame(valid_tick_frame())


def test_validate_bar_frame_accepts_valid_frame() -> None:
    validate_bar_frame(valid_bar_frame())


@pytest.mark.parametrize("column", TICK_REQUIRED_COLUMNS)
def test_validate_tick_frame_rejects_missing_required_columns(column: str) -> None:
    frame = valid_tick_frame().drop(columns=[column])

    with pytest.raises(SchemaValidationError, match="missing required columns"):
        validate_tick_frame(frame)


@pytest.mark.parametrize("column", BAR_REQUIRED_COLUMNS)
def test_validate_bar_frame_rejects_missing_required_columns(column: str) -> None:
    frame = valid_bar_frame().drop(columns=[column])

    with pytest.raises(SchemaValidationError, match="missing required columns"):
        validate_bar_frame(frame)


def test_validate_tick_frame_rejects_empty_frame_by_default() -> None:
    frame = valid_tick_frame().iloc[0:0]

    with pytest.raises(SchemaValidationError, match="must not be empty"):
        validate_tick_frame(frame)


def test_validate_bar_frame_rejects_empty_frame_by_default() -> None:
    frame = valid_bar_frame().iloc[0:0]

    with pytest.raises(SchemaValidationError, match="must not be empty"):
        validate_bar_frame(frame)


def test_validate_tick_frame_allows_empty_frame_when_requested() -> None:
    validate_tick_frame(valid_tick_frame().iloc[0:0], allow_empty=True)


def test_validate_bar_frame_allows_empty_frame_when_requested() -> None:
    validate_bar_frame(valid_bar_frame().iloc[0:0], allow_empty=True)


@pytest.mark.parametrize("column", ["price", "volume"])
def test_validate_tick_frame_rejects_non_numeric_price_volume(column: str) -> None:
    frame = valid_tick_frame()
    frame[column] = "bad"

    with pytest.raises(SchemaValidationError, match="must be numeric"):
        validate_tick_frame(frame)


@pytest.mark.parametrize("column", ["open", "high", "low", "close", "volume"])
def test_validate_bar_frame_rejects_non_numeric_ohlcv(column: str) -> None:
    frame = valid_bar_frame()
    frame[column] = "bad"

    with pytest.raises(SchemaValidationError, match="must be numeric"):
        validate_bar_frame(frame)


def test_validate_tick_frame_enforces_tmf_symbol() -> None:
    frame = valid_tick_frame()
    frame.loc[0, "symbol"] = "TX"

    with pytest.raises(SchemaValidationError, match="TMF"):
        validate_tick_frame(frame)


def test_validate_bar_frame_enforces_tmf_symbol() -> None:
    frame = valid_bar_frame()
    frame.loc[0, "symbol"] = "MTX"

    with pytest.raises(SchemaValidationError, match="TMF"):
        validate_bar_frame(frame)


def test_validate_bar_frame_enforces_v1_timeframes() -> None:
    frame = valid_bar_frame()
    frame.loc[0, "timeframe"] = "15m"

    with pytest.raises(SchemaValidationError, match="timeframes"):
        validate_bar_frame(frame)

