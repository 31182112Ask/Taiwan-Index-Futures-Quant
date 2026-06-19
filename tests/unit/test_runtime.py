from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import psutil
import pytest

from tifq.runtime import locking
from tifq.runtime.cleanup import (
    CleanupAction,
    CleanupPlan,
    apply_confirmed_cleanup,
    apply_safe_cleanup,
    build_cleanup_plan,
)
from tifq.runtime.health import run_environment_health_check
from tifq.runtime.locking import (
    OperationLock,
    OperationLockError,
    operation_lock_is_active,
    remove_stale_operation_locks,
)
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


def _write_lock(path: Path, *, pid: int, create_time: float) -> None:
    path.write_text(
        json.dumps(
            {
                "operation": "test",
                "pid": pid,
                "process_create_time": create_time,
                "started_at": "2026-06-20T00:00:00+00:00",
                "path": str(path),
            }
        ),
        encoding="utf-8",
    )


def test_pid_probe_never_calls_os_kill_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / "active.lock"
    _write_lock(
        lock_path,
        pid=os.getpid(),
        create_time=psutil.Process(os.getpid()).create_time(),
    )
    monkeypatch.setattr(
        locking.os,
        "kill",
        lambda *_args: pytest.fail("PID probing must never signal a process"),
    )

    assert operation_lock_is_active(lock_path)


def test_live_process_with_matching_create_time_is_active(tmp_path: Path) -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        lock_path = tmp_path / "child.lock"
        _write_lock(
            lock_path,
            pid=child.pid,
            create_time=psutil.Process(child.pid).create_time(),
        )

        assert operation_lock_is_active(lock_path)
        assert child.poll() is None
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_reused_pid_with_different_create_time_is_stale(tmp_path: Path) -> None:
    lock_path = tmp_path / "reused.lock"
    _write_lock(
        lock_path,
        pid=os.getpid(),
        create_time=psutil.Process(os.getpid()).create_time() - 60,
    )

    assert not operation_lock_is_active(lock_path)


def test_access_denied_process_is_treated_as_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / "denied.lock"
    _write_lock(lock_path, pid=12345, create_time=1.0)

    class DeniedProcess:
        def __init__(self, _pid: int) -> None:
            pass

        def create_time(self) -> float:
            raise psutil.AccessDenied(12345)

    monkeypatch.setattr(locking.psutil, "Process", DeniedProcess)

    assert operation_lock_is_active(lock_path)


def test_dead_process_lock_is_recoverable(tmp_path: Path) -> None:
    lock_path = tmp_path / "dead.lock"
    _write_lock(lock_path, pid=2_000_000_000, create_time=1.0)

    assert remove_stale_operation_locks(tmp_path) == (lock_path,)
    assert not lock_path.exists()


def test_active_process_is_never_terminated(tmp_path: Path) -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        lock_path = tmp_path / "active.lock"
        _write_lock(
            lock_path,
            pid=child.pid,
            create_time=psutil.Process(child.pid).create_time(),
        )

        assert remove_stale_operation_locks(tmp_path) == ()
        assert lock_path.exists()
        assert child.poll() is None
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_malformed_lock_is_preserved_while_pipeline_is_active(tmp_path: Path) -> None:
    pipeline = OperationLock(tmp_path, "import", lock_name="data_pipeline")
    pipeline.acquire()
    malformed = tmp_path / "import.lock"
    malformed.write_text("broken", encoding="utf-8")
    try:
        with pytest.raises(OperationLockError, match="cannot be recovered"):
            OperationLock(tmp_path, "import").acquire()
        assert malformed.exists()
    finally:
        pipeline.release()


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
