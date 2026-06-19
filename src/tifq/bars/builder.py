"""Build and store V1 OHLCV bar files from cleaned tick Parquet files."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from tifq.bars.resampler import resample_ticks_to_bars
from tifq.data.schemas import V1_SYMBOL, V1_TIMEFRAMES, validate_bar_frame
from tifq.data.storage import bar_path, read_parquet, write_parquet
from tifq.runtime.locking import OperationLock
from tifq.runtime.manifests import (
    BAR_MANIFEST_FILENAME,
    atomic_write_json,
    fingerprint_file,
    load_json_manifest,
    sha256_file,
)
from tifq.runtime.progress import ProgressCallback, ProgressReporter


@dataclass(frozen=True)
class BarBuildSummary:
    """Summary of a bar build run."""

    tick_files_read: int
    input_tick_count: int
    output_bar_count: int
    output_paths: tuple[Path, ...]
    tick_files_skipped: int = 0
    tick_files_rebuilt: int = 0
    manifest_path: Path | None = None
    no_op: bool = False


BUILDER_VERSION = "bars-v2"


def build_bar_files(
    processed_dir: str | Path,
    *,
    symbol: str = V1_SYMBOL,
    timeframe: str,
    force: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> BarBuildSummary:
    """Incrementally build daily OHLCV files from changed tick Parquet files."""
    _validate_symbol(symbol)
    _validate_timeframe(timeframe)

    processed_path = Path(processed_dir)
    tick_files = discover_tick_files(processed_path, symbol)
    manifest_path = processed_path / BAR_MANIFEST_FILENAME
    if not tick_files:
        return BarBuildSummary(0, 0, 0, (), manifest_path=manifest_path, no_op=True)

    quarantine = processed_path.parent / "quarantine" / "manifests"
    manifest = load_json_manifest(
        manifest_path,
        default={"version": 1, "records": []},
        quarantine_dir=quarantine,
    )
    records = _bar_manifest_records(manifest)
    reporter = ProgressReporter("bar_build", progress_callback)
    reporter.update("Build bars", 0, len(tick_files), "Planning incremental bar build")
    files_read = 0
    files_skipped = 0
    files_rebuilt = 0
    input_ticks = 0
    output_bars = 0
    output_paths: list[Path] = []

    with OperationLock(processed_path.parent / ".runtime", "bar_build"):
        updated_records = dict(records)
        for index, tick_file in enumerate(tick_files, start=1):
            key = f"{tick_file.resolve()}|{timeframe}"
            previous = records.get(key)
            fingerprint = fingerprint_file(tick_file, previous)
            if not force and _unchanged_bar_record(previous, fingerprint.sha256, timeframe):
                files_skipped += 1
                reporter.update(
                    "Build bars", index, len(tick_files), f"Unchanged: {tick_file.name}"
                )
                continue

            ticks = read_parquet(tick_file)
            bars = resample_ticks_to_bars(ticks, timeframe=timeframe, symbol=symbol)
            written = _write_daily_bars(bars, processed_path, symbol, timeframe)
            files_read += 1
            files_rebuilt += 1
            input_ticks += len(ticks)
            output_bars += len(bars)
            output_paths.extend(written)
            updated_records[key] = {
                "tick_path": str(tick_file.resolve()),
                "tick_hash": fingerprint.sha256,
                "size": fingerprint.size,
                "mtime_ns": fingerprint.mtime_ns,
                "timeframe": timeframe,
                "builder_version": BUILDER_VERSION,
                "built_at": datetime.now(tz=UTC).isoformat(),
                "output_paths": [str(output.resolve()) for output in written],
                "output_hashes": {
                    str(output.resolve()): sha256_file(output) for output in written
                },
            }
            reporter.update("Build bars", index, len(tick_files), f"Built: {tick_file.name}")

        if files_rebuilt:
            atomic_write_json(
                manifest_path,
                {
                    "version": 1,
                    "builder_version": BUILDER_VERSION,
                    "records": list(updated_records.values()),
                },
            )
    reporter.update("Complete", len(tick_files), len(tick_files), "Bar build complete")
    return BarBuildSummary(
        tick_files_read=files_read,
        input_tick_count=input_ticks,
        output_bar_count=output_bars,
        output_paths=tuple(sorted(set(output_paths))),
        tick_files_skipped=files_skipped,
        tick_files_rebuilt=files_rebuilt,
        manifest_path=manifest_path,
        no_op=files_rebuilt == 0,
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


def _bar_manifest_records(manifest: object) -> dict[str, dict[str, object]]:
    if not isinstance(manifest, dict) or not isinstance(manifest.get("records"), list):
        return {}
    records: dict[str, dict[str, object]] = {}
    for record in manifest["records"]:
        if not isinstance(record, dict):
            continue
        tick_path = record.get("tick_path")
        timeframe = record.get("timeframe")
        if isinstance(tick_path, str) and isinstance(timeframe, str):
            records[f"{tick_path}|{timeframe}"] = record
    return records


def _unchanged_bar_record(
    record: dict[str, object] | None,
    tick_hash: str,
    timeframe: str,
) -> bool:
    if record is None:
        return False
    if record.get("builder_version") != BUILDER_VERSION:
        return False
    if record.get("tick_hash") != tick_hash or record.get("timeframe") != timeframe:
        return False
    output_paths = record.get("output_paths")
    if not isinstance(output_paths, list):
        return False
    return all(Path(str(path)).exists() for path in output_paths)
