from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tifq.runtime.cleanup import (
    CleanupAction,
    CleanupPlan,
    apply_confirmed_cleanup,
    apply_safe_cleanup,
    build_cleanup_plan,
)
from tifq.runtime.health import run_environment_health_check
from tifq.runtime.locking import OperationLock, OperationLockError
from tifq.runtime.manifests import load_json_manifest
from tifq.runtime.progress import ProgressReporter


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def test_progress_is_bounded_and_eta_requires_samples() -> None:
    clock = FakeClock()
    updates = []
    reporter = ProgressReporter("import", updates.append, clock=clock)

    first = reporter.update("Import", 0, 3, "starting")
    clock.value = 2.0
    second = reporter.update("Import", 1, 3, "one")
    clock.value = 4.0
    third = reporter.update("Import", 2, 3, "two")
    completed = reporter.update("Complete", 8, 3, "done")

    assert first.eta_seconds is None
    assert second.eta_seconds is None
    assert third.eta_seconds == 2.0
    assert completed.completed == 3
    assert completed.percent == 1.0
    assert len(updates) == 4


def test_progress_callback_failure_does_not_change_operation() -> None:
    def broken_callback(update: object) -> None:
        raise RuntimeError("presentation failed")

    update = ProgressReporter("build", broken_callback).update("Build bars", 1, 1, "done")

    assert update.completed == 1


def test_operation_lock_blocks_active_owner_and_releases(tmp_path: Path) -> None:
    first = OperationLock(tmp_path, "bar build")
    first.acquire()

    with pytest.raises(OperationLockError, match="already running"):
        OperationLock(tmp_path, "bar build").acquire()

    first.release()
    with OperationLock(tmp_path, "bar build"):
        assert (tmp_path / "bar_build.lock").exists()
    assert not (tmp_path / "bar_build.lock").exists()


def test_operation_lock_recovers_dead_pid(tmp_path: Path) -> None:
    lock_path = tmp_path / "import.lock"
    lock_path.write_text(
        json.dumps(
            {"operation": "import", "pid": 2_000_000_000, "started_at": "old"}
        ),
        encoding="utf-8",
    )

    with OperationLock(tmp_path, "import"):
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        assert payload["pid"] == os.getpid()


def test_cleanup_plan_only_marks_stale_temp_as_automatic(tmp_path: Path) -> None:
    raw = tmp_path / "data" / "raw" / "taifex"
    processed = tmp_path / "data" / "processed"
    results = tmp_path / "data" / "results" / "backtests" / "run"
    raw.mkdir(parents=True)
    processed.mkdir(parents=True)
    results.mkdir(parents=True)
    stale = raw / "download.zip.part"
    fresh = raw / "fresh.csv.part"
    raw_file = raw / "manual.csv"
    parquet = processed / "ticks.parquet"
    result = results / "metrics.json"
    for path in (stale, fresh, raw_file, parquet, result):
        path.write_bytes(b"data")
    os.utime(stale, (1, 1))

    plan = build_cleanup_plan(tmp_path, temp_ttl_seconds=60)

    assert [action.path for action in plan.actions] == [stale]
    summary = apply_safe_cleanup(plan, tmp_path)
    assert not stale.exists()
    assert fresh.exists()
    assert raw_file.exists()
    assert parquet.exists()
    assert result.exists()
    assert summary.bytes_reclaimed == 4


def test_safe_cleanup_rejects_path_outside_repository(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.part"
    outside.write_bytes(b"keep")
    action = CleanupAction("delete_temp", outside, "test", 4, True)
    plan = CleanupPlan((action,), 4, 1, 0)

    summary = apply_safe_cleanup(plan, tmp_path)

    assert outside.exists()
    assert summary.failed
    outside.unlink()


def test_duplicate_raw_file_requires_confirmed_quarantine(tmp_path: Path) -> None:
    raw = tmp_path / "data" / "raw" / "taifex"
    raw.mkdir(parents=True)
    first = raw / "first.csv"
    second = raw / "second.csv"
    first.write_bytes(b"same")
    second.write_bytes(b"same")

    plan = build_cleanup_plan(tmp_path, full_scan=True)

    assert plan.safe_action_count == 0
    assert plan.confirmation_action_count == 1
    dry_run_paths = {path for path in (first, second) if path.exists()}
    assert dry_run_paths == {first, second}

    summary = apply_confirmed_cleanup(plan.actions, tmp_path)
    assert len(summary.applied) == 1
    assert summary.applied[0].destination is not None
    assert summary.applied[0].destination.exists()
    assert sum(path.exists() for path in (first, second)) == 1


def test_corrupt_manifest_is_quarantined_before_rebuild(tmp_path: Path) -> None:
    manifest = tmp_path / "import_manifest.json"
    quarantine = tmp_path / "quarantine"
    manifest.write_text("{broken", encoding="utf-8")

    payload = load_json_manifest(manifest, default={"records": []}, quarantine_dir=quarantine)

    assert payload == {"records": []}
    assert not manifest.exists()
    assert len(list(quarantine.glob("import_manifest.json.*.corrupt"))) == 1


def test_health_check_reports_corrupt_manifest_without_modifying_it(tmp_path: Path) -> None:
    for directory in (
        tmp_path / "data" / "raw" / "taifex",
        tmp_path / "data" / "processed",
        tmp_path / "data" / "results" / "backtests",
        tmp_path / "logs",
    ):
        directory.mkdir(parents=True)
    manifest = tmp_path / "data" / "processed" / "import_manifest.json"
    manifest.write_text("not json", encoding="utf-8")

    report = run_environment_health_check(tmp_path)

    assert report.status == "error"
    assert any(issue.code == "corrupt_manifest" for issue in report.issues)
    assert manifest.exists()
