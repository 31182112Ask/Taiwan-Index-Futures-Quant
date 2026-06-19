from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from tifq.bars import build_bar_files, discover_tick_files, resample_ticks_to_bars
from tifq.data.storage import read_parquet, tick_path, write_parquet


def tick_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["TMF", "TMF", "TMF", "TMF", "TMF"],
            "contract": ["202606", "202606", "202606", "202606", "202606"],
            "timestamp": pd.to_datetime(
                [
                    "2026-06-17 08:45:00",
                    "2026-06-17 08:45:30",
                    "2026-06-17 08:47:00",
                    "2026-06-18 08:45:00",
                    "2026-06-18 13:46:00",
                ]
            ).tz_localize("Asia/Taipei"),
            "price": [22000.0, 22002.0, 22001.0, 22100.0, 23000.0],
            "volume": [1, 2, 3, 4, 99],
            "source": ["unit-test"] * 5,
        }
    )


def test_resample_ticks_to_1m_bars_aggregates_ohlcv_and_skips_empty_minutes() -> None:
    bars = resample_ticks_to_bars(tick_frame(), timeframe="1m")

    first_bar = bars.iloc[0]
    assert first_bar["timestamp"] == pd.Timestamp("2026-06-17 08:45:00", tz="Asia/Taipei")
    assert first_bar["open"] == 22000
    assert first_bar["high"] == 22002
    assert first_bar["low"] == 22000
    assert first_bar["close"] == 22002
    assert first_bar["volume"] == 3
    assert pd.Timestamp("2026-06-17 08:46:00", tz="Asia/Taipei") not in set(bars["timestamp"])


def test_resample_ticks_to_bars_does_not_mix_trading_days() -> None:
    bars = resample_ticks_to_bars(tick_frame(), timeframe="1m")

    assert bars["timestamp"].dt.date.tolist() == [
        date(2026, 6, 17),
        date(2026, 6, 17),
        date(2026, 6, 18),
    ]
    day_two_bar = bars.loc[bars["timestamp"].dt.date == date(2026, 6, 18)].iloc[0]
    assert day_two_bar["open"] == 22100
    assert day_two_bar["volume"] == 4


def test_resample_ticks_to_5m_bars_aggregates_on_five_minute_boundaries() -> None:
    ticks = pd.DataFrame(
        {
            "symbol": ["TMF", "TMF", "TMF"],
            "contract": ["202606", "202606", "202606"],
            "timestamp": pd.to_datetime(
                [
                    "2026-06-17 08:45:00",
                    "2026-06-17 08:49:59",
                    "2026-06-17 08:50:00",
                ]
            ).tz_localize("Asia/Taipei"),
            "price": [22000.0, 22005.0, 22010.0],
            "volume": [1, 2, 3],
            "source": ["unit-test"] * 3,
        }
    )

    bars = resample_ticks_to_bars(ticks, timeframe="5m")

    assert bars["timestamp"].tolist() == [
        pd.Timestamp("2026-06-17 08:45:00", tz="Asia/Taipei"),
        pd.Timestamp("2026-06-17 08:50:00", tz="Asia/Taipei"),
    ]
    assert bars["close"].tolist() == [22005, 22010]
    assert bars["volume"].tolist() == [3, 3]


def test_resample_ticks_to_bars_rejects_invalid_timeframe() -> None:
    with pytest.raises(ValueError, match="timeframes"):
        resample_ticks_to_bars(tick_frame(), timeframe="15m")


def test_build_bar_files_reads_ticks_and_writes_daily_bar_parquet(tmp_path: Path) -> None:
    processed_dir = tmp_path / "processed"
    day_one_ticks = tick_frame().loc[0:2].reset_index(drop=True)
    day_two_ticks = tick_frame().loc[[3]].reset_index(drop=True)
    write_parquet(day_one_ticks, tick_path(processed_dir, "TMF", date(2026, 6, 17)))
    write_parquet(day_two_ticks, tick_path(processed_dir, "TMF", date(2026, 6, 18)))

    summary = build_bar_files(processed_dir, symbol="TMF", timeframe="1m")

    assert summary.tick_files_read == 2
    assert summary.input_tick_count == 4
    assert summary.output_bar_count == 3
    assert summary.output_paths == (
        processed_dir / "bars" / "TMF" / "1m" / "2026-06-17.parquet",
        processed_dir / "bars" / "TMF" / "1m" / "2026-06-18.parquet",
    )
    day_one_bars = read_parquet(summary.output_paths[0])
    assert day_one_bars["timeframe"].tolist() == ["1m", "1m"]
    assert day_one_bars["volume"].tolist() == [3, 3]


def test_build_bar_files_empty_tick_directory_returns_empty_summary(tmp_path: Path) -> None:
    summary = build_bar_files(tmp_path / "processed", symbol="TMF", timeframe="5m")

    assert summary.tick_files_read == 0
    assert summary.input_tick_count == 0
    assert summary.output_bar_count == 0
    assert summary.output_paths == ()


def test_zero_output_tick_file_is_unchanged_on_second_build(tmp_path: Path) -> None:
    processed_dir = tmp_path / "processed"
    ticks = tick_frame().loc[[0]].reset_index(drop=True)
    ticks["timestamp"] = pd.to_datetime(["2026-06-17 14:00:00"]).tz_localize(
        "Asia/Taipei"
    )
    write_parquet(ticks, tick_path(processed_dir, "TMF", date(2026, 6, 17)))

    first = build_bar_files(processed_dir, symbol="TMF", timeframe="5m")
    second = build_bar_files(processed_dir, symbol="TMF", timeframe="5m")

    assert first.tick_files_read == 1
    assert first.output_bar_count == 0
    assert second.tick_files_read == 0
    assert second.tick_files_skipped == 1
    assert second.no_op is True


def test_discover_tick_files_returns_sorted_parquet_files(tmp_path: Path) -> None:
    tick_dir = tmp_path / "processed" / "ticks" / "TMF"
    tick_dir.mkdir(parents=True)
    write_parquet(tick_frame().loc[[3]], tick_dir / "2026-06-18.parquet")
    write_parquet(tick_frame().loc[[0]], tick_dir / "2026-06-17.parquet")
    (tick_dir / "notes.txt").write_text("ignored", encoding="utf-8")

    assert discover_tick_files(tmp_path / "processed") == [
        tick_dir / "2026-06-17.parquet",
        tick_dir / "2026-06-18.parquet",
    ]


def test_incremental_bar_build_second_run_does_not_read_or_rewrite_tick(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processed_dir = tmp_path / "processed"
    tick_output = tick_path(processed_dir, "TMF", date(2026, 6, 17))
    write_parquet(tick_frame().loc[0:2].reset_index(drop=True), tick_output)
    first = build_bar_files(processed_dir, symbol="TMF", timeframe="5m")
    output = first.output_paths[0]
    output_mtime = output.stat().st_mtime_ns

    monkeypatch.setattr(
        "tifq.bars.builder.read_parquet",
        lambda path: (_ for _ in ()).throw(AssertionError(f"unexpected read: {path}")),
    )
    second = build_bar_files(processed_dir, symbol="TMF", timeframe="5m")

    assert second.no_op is True
    assert second.tick_files_read == 0
    assert second.tick_files_skipped == 1
    assert output.stat().st_mtime_ns == output_mtime


def test_bar_manifest_tracks_timeframes_separately(tmp_path: Path) -> None:
    processed_dir = tmp_path / "processed"
    write_parquet(
        tick_frame().loc[0:2].reset_index(drop=True),
        tick_path(processed_dir, "TMF", date(2026, 6, 17)),
    )

    build_bar_files(processed_dir, symbol="TMF", timeframe="1m")
    build_bar_files(processed_dir, symbol="TMF", timeframe="5m")

    payload = json.loads((processed_dir / "bar_manifest.json").read_text(encoding="utf-8"))
    assert {record["timeframe"] for record in payload["records"]} == {"1m", "5m"}


def test_bar_build_failure_does_not_modify_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processed_dir = tmp_path / "processed"
    tick_output = tick_path(processed_dir, "TMF", date(2026, 6, 17))
    write_parquet(tick_frame().loc[0:2].reset_index(drop=True), tick_output)
    build_bar_files(processed_dir, symbol="TMF", timeframe="5m")
    manifest = processed_dir / "bar_manifest.json"
    before = manifest.read_bytes()
    changed = tick_frame().loc[0:2].reset_index(drop=True)
    changed.loc[0, "price"] = 999.0
    write_parquet(changed, tick_output)
    monkeypatch.setattr(
        "tifq.bars.builder.resample_ticks_to_bars",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("build failed")),
    )

    with pytest.raises(RuntimeError, match="build failed"):
        build_bar_files(processed_dir, symbol="TMF", timeframe="5m")

    assert manifest.read_bytes() == before
