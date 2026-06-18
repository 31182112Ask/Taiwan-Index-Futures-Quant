from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from tifq.data.storage import bar_path, read_parquet, tick_path, write_parquet


def test_write_and_read_parquet_round_trip(tmp_path: Path) -> None:
    frame = pd.DataFrame(
        {
            "symbol": ["TMF"],
            "price": [22000.0],
            "volume": [1],
        }
    )
    path = tmp_path / "nested" / "ticks.parquet"

    write_parquet(frame, path)
    loaded = read_parquet(path)

    pd.testing.assert_frame_equal(loaded, frame)


def test_tick_path_uses_v1_processed_layout() -> None:
    path = tick_path(Path("data/processed"), "TMF", date(2026, 6, 17))

    assert path == Path("data/processed/ticks/TMF/2026-06-17.parquet")


def test_bar_path_uses_v1_processed_layout() -> None:
    path = bar_path(Path("data/processed"), "TMF", "5m", date(2026, 6, 17))

    assert path == Path("data/processed/bars/TMF/5m/2026-06-17.parquet")


@pytest.mark.parametrize("symbol", ["TX", "MTX", ""])
def test_tick_path_enforces_tmf_symbol(symbol: str) -> None:
    with pytest.raises(ValueError, match="TMF"):
        tick_path(Path("data/processed"), symbol, date(2026, 6, 17))


@pytest.mark.parametrize("symbol", ["TX", "MTX", ""])
def test_bar_path_enforces_tmf_symbol(symbol: str) -> None:
    with pytest.raises(ValueError, match="TMF"):
        bar_path(Path("data/processed"), symbol, "5m", date(2026, 6, 17))


@pytest.mark.parametrize("timeframe", ["15m", "1h", ""])
def test_bar_path_enforces_v1_timeframes(timeframe: str) -> None:
    with pytest.raises(ValueError, match="timeframes"):
        bar_path(Path("data/processed"), "TMF", timeframe, date(2026, 6, 17))

