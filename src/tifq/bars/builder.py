"""Build and store V1 OHLCV bar files from cleaned tick Parquet files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from tifq.bars.resampler import resample_ticks_to_bars
from tifq.data.schemas import V1_SYMBOL, V1_TIMEFRAMES, validate_bar_frame
from tifq.data.storage import bar_path, read_parquet, write_parquet


@dataclass(frozen=True)
class BarBuildSummary:
    """Summary of a bar build run."""

    tick_files_read: int
    input_tick_count: int
    output_bar_count: int
    output_paths: tuple[Path, ...]


def build_bar_files(
    processed_dir: str | Path,
    *,
    symbol: str = V1_SYMBOL,
    timeframe: str,
) -> BarBuildSummary:
    """Read cleaned tick Parquet files and write daily OHLCV bar Parquet files."""
    _validate_symbol(symbol)
    _validate_timeframe(timeframe)

    processed_path = Path(processed_dir)
    tick_files = discover_tick_files(processed_path, symbol)
    if not tick_files:
        return BarBuildSummary(0, 0, 0, ())

    tick_frames = [read_parquet(path) for path in tick_files]
    ticks = pd.concat(tick_frames, ignore_index=True)
    bars = resample_ticks_to_bars(ticks, timeframe=timeframe, symbol=symbol)
    output_paths = _write_daily_bars(bars, processed_path, symbol, timeframe)

    return BarBuildSummary(
        tick_files_read=len(tick_files),
        input_tick_count=len(ticks),
        output_bar_count=len(bars),
        output_paths=tuple(output_paths),
    )


def discover_tick_files(processed_dir: str | Path, symbol: str = V1_SYMBOL) -> list[Path]:
    """Return sorted cleaned tick Parquet files under the V1 processed layout."""
    _validate_symbol(symbol)
    tick_dir = Path(processed_dir) / "ticks" / symbol
    if not tick_dir.exists():
        return []
    if not tick_dir.is_dir():
        raise NotADirectoryError(f"Tick path is not a directory: {tick_dir}")
    return sorted(
        path
        for path in tick_dir.iterdir()
        if path.is_file() and path.suffix == ".parquet"
    )


def _write_daily_bars(
    bars: pd.DataFrame,
    processed_dir: Path,
    symbol: str,
    timeframe: str,
) -> list[Path]:
    if bars.empty:
        return []

    validate_bar_frame(bars)
    output_paths: list[Path] = []
    for trading_date, daily_bars in bars.groupby(bars["timestamp"].dt.date, sort=True):
        output_path = bar_path(processed_dir, symbol, timeframe, trading_date)
        write_parquet(daily_bars.reset_index(drop=True), output_path)
        output_paths.append(output_path)
    return output_paths


def _validate_symbol(symbol: str) -> None:
    if symbol != V1_SYMBOL:
        raise ValueError(f"V1 supports symbol {V1_SYMBOL} only; got: {symbol}")


def _validate_timeframe(timeframe: str) -> None:
    if timeframe not in V1_TIMEFRAMES:
        allowed = ", ".join(sorted(V1_TIMEFRAMES))
        raise ValueError(f"V1 supports timeframes {{{allowed}}} only; got: {timeframe}")
