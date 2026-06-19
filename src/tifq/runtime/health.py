"""Fast and full environment health checks for local V1 runtime state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Literal

from tifq.runtime.cleanup import CleanupPlan, build_cleanup_plan
from tifq.runtime.locking import active_operation_locks
from tifq.runtime.manifests import BAR_MANIFEST_FILENAME, IMPORT_MANIFEST_FILENAME
from tifq.runtime.progress import ProgressCallback, ProgressReporter

Severity = Literal["info", "warning", "error"]


@dataclass(frozen=True)
class HealthIssue:
    """One actionable environment observation."""

    code: str
    severity: Severity
    path: Path | None
    message: str
    recoverable: bool


@dataclass(frozen=True)
class HealthReport:
    """Health result safe for CLI and UI rendering."""

    status: Literal["healthy", "warning", "error"]
    checked_at: str
    duration_seconds: float
    healthy_files: int
    issues: tuple[HealthIssue, ...]
    cleanup_plan: CleanupPlan
    active_operations: tuple[str, ...]


def run_environment_health_check(
    repository_root: str | Path | None = None,
    *,
    full_scan: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> HealthReport:
    """Inspect structure, manifests, temporary files, locks, and optional duplicates."""
    started = perf_counter()
    reporter = ProgressReporter("health_check", progress_callback)
    root = Path.cwd().resolve() if repository_root is None else Path(repository_root).resolve()
    issues: list[HealthIssue] = []
    required_dirs = (
        root / "data" / "raw" / "taifex",
        root / "data" / "processed",
        root / "data" / "results" / "backtests",
        root / "logs",
    )
    reporter.update("Preflight", 0, len(required_dirs), "Checking runtime directories")
    healthy_files = 0
    for index, directory in enumerate(required_dirs, start=1):
        if directory.exists() and directory.is_dir():
            healthy_files += 1
        else:
            issues.append(
                HealthIssue(
                    "missing_directory",
                    "warning",
                    directory,
                    "Required runtime directory is missing and can be recreated.",
                    True,
                )
            )
        reporter.update("Preflight", index, len(required_dirs), str(directory))

    manifest_paths = (
        root / "data" / "raw" / "taifex" / "download_manifest.json",
        root / "data" / "processed" / IMPORT_MANIFEST_FILENAME,
        root / "data" / "processed" / BAR_MANIFEST_FILENAME,
    )
    for manifest_path in manifest_paths:
        if not manifest_path.exists():
            continue
        try:
            json.loads(manifest_path.read_text(encoding="utf-8"))
            healthy_files += 1
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            issues.append(
                HealthIssue(
                    "corrupt_manifest",
                    "error",
                    manifest_path,
                    f"Manifest cannot be parsed: {exc}",
                    True,
                )
            )

    cleanup_plan = build_cleanup_plan(root, full_scan=full_scan)
    if cleanup_plan.safe_action_count:
        issues.append(
            HealthIssue(
                "stale_temporary_files",
                "warning",
                None,
                f"{cleanup_plan.safe_action_count} stale temporary files can be removed safely.",
                True,
            )
        )
    if cleanup_plan.confirmation_action_count:
        issues.append(
            HealthIssue(
                "cleanup_confirmation_required",
                "warning",
                None,
                f"{cleanup_plan.confirmation_action_count} cleanup actions require review.",
                True,
            )
        )
    locks = active_operation_locks(root / "data" / ".runtime")
    active = tuple(f"{lock.operation} (PID {lock.pid})" for lock in locks)
    if active:
        issues.append(
            HealthIssue(
                "active_operation",
                "warning",
                None,
                "; ".join(active),
                False,
            )
        )
    status: Literal["healthy", "warning", "error"] = "healthy"
    if any(issue.severity == "error" for issue in issues):
        status = "error"
    elif issues:
        status = "warning"
    reporter.update("Complete", 1, 1, f"Environment status: {status}")
    return HealthReport(
        status=status,
        checked_at=datetime.now(tz=UTC).isoformat(),
        duration_seconds=perf_counter() - started,
        healthy_files=healthy_files,
        issues=tuple(issues),
        cleanup_plan=cleanup_plan,
        active_operations=active,
    )
