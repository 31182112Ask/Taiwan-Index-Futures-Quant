from __future__ import annotations

import pandas as pd
import pytest

from tifq.data.tick_cleaner import clean_tick_frame


def normalized_tick_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["TMF", "TX", "TMF", "TMF", "TMF"],
            "contract": ["202606", "202606", "202606", "202606", "202606"],
            "timestamp": [
                "2026-06-17 08:45:00",
                "2026-06-17 08:45:01",
                "bad timestamp",
                "2026-06-17 08:45:03",
                "2026-06-17 08:45:02",
            ],
            "price": ["22000", "22001", "22002", "-1", "22,003"],
            "volume": ["1", "2", "3", "4", "5"],
            "source": ["sample.csv"] * 5,
        }
    )


def test_clean_tick_frame_filters_invalid_rows_and_sorts() -> None:
    result = clean_tick_frame(normalized_tick_frame())

    assert result.input_row_count == 5
    assert result.invalid_row_count == 3
    assert len(result.ticks) == 2
    assert result.ticks["symbol"].tolist() == ["TMF", "TMF"]
    assert result.ticks["price"].tolist() == [22000, 22003]
    assert result.ticks["volume"].tolist() == [1, 5]
    assert result.ticks["timestamp"].dt.tz is not None
    assert result.ticks["timestamp"].is_monotonic_increasing


def test_clean_tick_frame_rejects_non_tmf_symbol_argument() -> None:
    with pytest.raises(ValueError, match="TMF"):
        clean_tick_frame(normalized_tick_frame(), symbol="TX")


def test_clean_tick_frame_requires_internal_columns() -> None:
    frame = normalized_tick_frame().drop(columns=["source"])

    with pytest.raises(ValueError, match="missing required columns"):
        clean_tick_frame(frame)

