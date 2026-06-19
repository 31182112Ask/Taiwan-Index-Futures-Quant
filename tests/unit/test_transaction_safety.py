from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from tifq.bars.builder import build_bar_files
from tifq.data.storage import read_parquet, tick_path, write_parquet
from tifq.data.taifex_fetcher import sync_recent_taifex_csv_files
from tifq.data.taifex_loader import import_taifex_ticks
from tifq.runtime.cleanup import CleanupPlan, apply_safe_cleanup, build_cleanup_plan
from tifq.runtime.health import run_environment_health_check
from tifq.runtime.locking import (
    OperationLockError,
    PipelineOperationLock,
    operation_lock_is_active,
)


def _write_raw(path: Path, trading_date: str, price: int = 22_000) -> None:
    path.write_text(
        f"symbol,contract,timestamp,price,volume\nTMF,202606,{trading_date} 08:45:00,{price},1\n",
        encoding="utf-8",
    )


def _tick_frame(trading_date: str, price: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["TMF"],
            "contract": ["202606"],
            "timestamp": [pd.Timestamp(f"{trading_date} 08:45", tz="Asia/Taipei")],
            "price": [price],
            "volume": [1],
            "source": ["unit"],
        }
    )


def test_import_second_file_failure_does_not_publish_first_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = tmp_path / "data" / "raw" / "taifex"
    processed = tmp_path / "data" / "processed"
    raw.mkdir(parents=True)
    _write_raw(raw / "first.csv", "2026-06-17")
    _write_raw(raw / "second.csv", "2026-06-18")
    from tifq.data import taifex_loader

    original = taifex_loader._read_raw_file

    def fail_second(path: Path):
        if path.name == "second.csv":
            raise RuntimeError("second source failed")
        return original(path)

    monkeypatch.setattr(taifex_loader, "_read_raw_file", fail_second)

    with pytest.raises(RuntimeError, match="second source failed"):
        import_taifex_ticks(raw, processed)

    assert not (processed / "import_manifest.json").exists()
    assert (
        not list((processed / "ticks").rglob("*.parquet"))
        if (processed / "ticks").exists()
        else True
    )


def test_import_failure_preserves_previous_outputs_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = tmp_path / "data" / "raw" / "taifex"
    processed = tmp_path / "data" / "processed"
    raw.mkdir(parents=True)
    first = raw / "first.csv"
    second = raw / "second.csv"
    _write_raw(first, "2026-06-17")
    _write_raw(second, "2026-06-18")
    summary = import_taifex_ticks(raw, processed)
    before_outputs = {path: path.read_bytes() for path in summary.output_paths}
    manifest = processed / "import_manifest.json"
    before_manifest = manifest.read_bytes()
    _write_raw(first, "2026-06-17", 23_000)
    _write_raw(second, "2026-06-18", 23_100)
    from tifq.data import taifex_loader

    original_write = taifex_loader.write_parquet
    writes = 0

    def fail_second_write(frame: pd.DataFrame, path: Path) -> None:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise RuntimeError("second staged output failed")
        original_write(frame, path)

    monkeypatch.setattr(taifex_loader, "write_parquet", fail_second_write)

    with pytest.raises(RuntimeError, match="second staged output failed"):
        import_taifex_ticks(raw, processed)

    assert manifest.read_bytes() == before_manifest
    assert {path: path.read_bytes() for path in summary.output_paths} == before_outputs


def test_modified_output_hash_forces_import_rebuild(tmp_path: Path) -> None:
    raw = tmp_path / "data" / "raw" / "taifex"
    processed = tmp_path / "data" / "processed"
    raw.mkdir(parents=True)
    _write_raw(raw / "ticks.csv", "2026-06-17")
    first = import_taifex_ticks(raw, processed)
    first.output_paths[0].write_bytes(b"corrupt")

    rebuilt = import_taifex_ticks(raw, processed)

    assert rebuilt.files_changed == 1
    assert len(read_parquet(first.output_paths[0])) == 1


def test_bar_build_second_file_failure_preserves_outputs_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processed = tmp_path / "data" / "processed"
    for day, price in (("2026-06-17", 100.0), ("2026-06-18", 200.0)):
        write_parquet(
            _tick_frame(day, price),
            tick_path(processed, "TMF", date.fromisoformat(day)),
        )
    first = build_bar_files(processed, timeframe="5m")
    before_outputs = {path: path.read_bytes() for path in first.output_paths}
    manifest = processed / "bar_manifest.json"
    before_manifest = manifest.read_bytes()
    for day, price in (("2026-06-17", 101.0), ("2026-06-18", 201.0)):
        write_parquet(
            _tick_frame(day, price),
            tick_path(processed, "TMF", date.fromisoformat(day)),
        )
    from tifq.bars import builder

    original_write = builder.write_parquet
    writes = 0

    def fail_second_write(frame: pd.DataFrame, path: Path) -> None:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise RuntimeError("second bar output failed")
        original_write(frame, path)

    monkeypatch.setattr(builder, "write_parquet", fail_second_write)

    with pytest.raises(RuntimeError, match="second bar output failed"):
        build_bar_files(processed, timeframe="5m")

    assert manifest.read_bytes() == before_manifest
    assert {path: path.read_bytes() for path in first.output_paths} == before_outputs


def test_modified_bar_hash_forces_bar_rebuild(tmp_path: Path) -> None:
    processed = tmp_path / "data" / "processed"
    tick_file = tick_path(processed, "TMF", date(2026, 6, 17))
    write_parquet(_tick_frame("2026-06-17", 100.0), tick_file)
    first = build_bar_files(processed, timeframe="5m")
    first.output_paths[0].write_bytes(b"corrupt")

    rebuilt = build_bar_files(processed, timeframe="5m")

    assert rebuilt.tick_files_rebuilt == 1
    assert len(read_parquet(first.output_paths[0])) == 1


def test_successful_publish_removes_staging_directory(tmp_path: Path) -> None:
    raw = tmp_path / "data" / "raw" / "taifex"
    processed = tmp_path / "data" / "processed"
    raw.mkdir(parents=True)
    _write_raw(raw / "ticks.csv", "2026-06-17")

    import_taifex_ticks(raw, processed)
    build_bar_files(processed, timeframe="5m")

    assert not list((processed / ".staging").iterdir())


def test_stale_staging_is_reported_by_doctor_and_removed_safely(tmp_path: Path) -> None:
    staging = tmp_path / "data" / "processed" / ".staging" / "import-stale"
    staging.mkdir(parents=True)
    (staging / "candidate.parquet").write_bytes(b"staged")
    os.utime(staging, (1, 1))

    report = run_environment_health_check(tmp_path)

    assert any(action.action == "delete_staging" for action in report.cleanup_plan.actions)
    apply_safe_cleanup(report.cleanup_plan, tmp_path)
    assert not staging.exists()


def test_import_and_bar_build_cannot_write_concurrently(tmp_path: Path) -> None:
    raw = tmp_path / "data" / "raw" / "taifex"
    processed = tmp_path / "data" / "processed"
    raw.mkdir(parents=True)
    _write_raw(raw / "ticks.csv", "2026-06-17")

    with PipelineOperationLock(tmp_path / "data" / ".runtime", "bar_build"):
        with pytest.raises(OperationLockError):
            import_taifex_ticks(raw, processed)


def test_cleanup_cannot_run_during_import(tmp_path: Path) -> None:
    plan = CleanupPlan((), 0, 0, 0)
    with PipelineOperationLock(tmp_path / "data" / ".runtime", "raw_import"):
        with pytest.raises(OperationLockError):
            apply_safe_cleanup(plan, tmp_path)


def test_sync_cannot_run_during_bar_build(tmp_path: Path) -> None:
    raw = tmp_path / "data" / "raw" / "taifex"
    with PipelineOperationLock(tmp_path / "data" / ".runtime", "bar_build"):
        with pytest.raises(OperationLockError):
            sync_recent_taifex_csv_files(raw, limit=1)


def test_lock_order_does_not_deadlock(tmp_path: Path) -> None:
    lock_dir = tmp_path / "data" / ".runtime"
    with PipelineOperationLock(lock_dir, "raw_import"):
        assert operation_lock_is_active(lock_dir / "data_pipeline.lock")
    with PipelineOperationLock(lock_dir, "bar_build"):
        assert operation_lock_is_active(lock_dir / "data_pipeline.lock")


def test_dead_shared_pipeline_lock_is_recovered(tmp_path: Path) -> None:
    lock_dir = tmp_path / "data" / ".runtime"
    lock_dir.mkdir(parents=True)
    shared = lock_dir / "data_pipeline.lock"
    shared.write_text(
        json.dumps({"operation": "old", "pid": 2_000_000_000, "started_at": "old"}),
        encoding="utf-8",
    )

    with PipelineOperationLock(lock_dir, "raw_import"):
        assert operation_lock_is_active(shared)

    shared.write_text(
        json.dumps({"operation": "old", "pid": 2_000_000_000, "started_at": "old"}),
        encoding="utf-8",
    )
    plan = build_cleanup_plan(tmp_path, temp_ttl_seconds=0)
    summary = apply_safe_cleanup(plan, tmp_path)
    assert not shared.exists()
    assert not summary.failed


def test_active_shared_pipeline_lock_is_preserved(tmp_path: Path) -> None:
    lock_dir = tmp_path / "data" / ".runtime"
    with PipelineOperationLock(lock_dir, "raw_import"):
        plan = build_cleanup_plan(tmp_path, temp_ttl_seconds=0)
        assert not any(action.path == lock_dir / "data_pipeline.lock" for action in plan.actions)
