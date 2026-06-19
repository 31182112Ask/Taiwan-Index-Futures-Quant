"""Conservative cleanup planning and explicitly separated apply operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import time
from typing import Literal

from tifq.runtime.locking import OperationLock
from tifq.runtime.manifests import sha256_file

CleanupKind = Literal["delete_temp", "quarantine", "keep", "rebuild"]
_TEMP_SUFFIXES = frozenset({".part", ".tmp", ".temp"})
_FORBIDDEN_ROOT_NAMES = frozenset({".git", ".venv", "src", "tests", "configs"})


@dataclass(frozen=True)
class CleanupAction:
    """One auditable cleanup recommendation."""

    action: CleanupKind
    path: Path
    reason: str
    size_bytes: int
    safe_to_apply_automatically: bool
    destination: Path | None = None


@dataclass(frozen=True)
class CleanupPlan:
    """Dry-run plan split into safe and confirmation-required actions."""

    actions: tuple[CleanupAction, ...]
    total_bytes: int
    safe_action_count: int
    confirmation_action_count: int


@dataclass(frozen=True)
class CleanupSummary:
    """Applied action log with failures and reclaimed bytes."""

    applied: tuple[CleanupAction, ...]
    failed: tuple[str, ...]
    bytes_reclaimed: int


def build_cleanup_plan(
    repository_root: str | Path,
    *,
    full_scan: bool = False,
    temp_ttl_seconds: float = 24 * 60 * 60,
    prune_results: bool = False,
    keep_latest: int = 20,
) -> CleanupPlan:
    """Build a read-only plan; valuable data is never marked automatic."""
    root = Path(repository_root).resolve()
    actions: list[CleanupAction] = []
    now = time()
    scan_roots = [root / "data", root / "logs"]
    for scan_root in scan_roots:
        if not scan_root.exists():
            continue
        for path in scan_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in _TEMP_SUFFIXES:
                continue
            age = now - path.stat().st_mtime
            if age >= temp_ttl_seconds:
                actions.append(
                    CleanupAction(
                        "delete_temp",
                        path,
                        f"stale disposable temporary file ({age:.0f}s old)",
                        path.stat().st_size,
                        True,
                    )
                )

    if full_scan:
        actions.extend(_duplicate_raw_actions(root))
    if prune_results:
        actions.extend(_old_result_actions(root, keep_latest))
    return _plan(actions)


def apply_safe_cleanup(
    plan: CleanupPlan,
    repository_root: str | Path,
) -> CleanupSummary:
    """Apply only allowlisted disposable-file deletions."""
    root = Path(repository_root).resolve()
    applied: list[CleanupAction] = []
    failed: list[str] = []
    reclaimed = 0
    with OperationLock(root / "data" / ".runtime", "cleanup_apply"):
        for action in plan.actions:
            if not action.safe_to_apply_automatically or action.action != "delete_temp":
                continue
            try:
                _validate_cleanup_path(action.path, root)
                if action.path.suffix.lower() not in _TEMP_SUFFIXES:
                    raise ValueError(f"not an allowlisted temporary file: {action.path}")
                action.path.unlink(missing_ok=True)
                applied.append(action)
                reclaimed += action.size_bytes
            except (OSError, ValueError) as exc:
                failed.append(f"{action.path}: {exc}")
    return CleanupSummary(tuple(applied), tuple(failed), reclaimed)


def apply_confirmed_cleanup(
    actions: tuple[CleanupAction, ...],
    repository_root: str | Path,
) -> CleanupSummary:
    """Quarantine explicitly confirmed valuable/conflicting files; never delete them."""
    root = Path(repository_root).resolve()
    quarantine_root = root / "data" / "quarantine" / datetime.now(tz=UTC).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
    applied: list[CleanupAction] = []
    failed: list[str] = []
    with OperationLock(root / "data" / ".runtime", "cleanup_apply"):
        for action in actions:
            if action.action != "quarantine" or action.safe_to_apply_automatically:
                continue
            try:
                source = _validate_cleanup_path(action.path, root)
                relative = source.relative_to(root)
                destination = quarantine_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                source.replace(destination)
                applied.append(
                    CleanupAction(
                        action.action,
                        source,
                        action.reason,
                        action.size_bytes,
                        False,
                        destination,
                    )
                )
            except (OSError, ValueError) as exc:
                failed.append(f"{action.path}: {exc}")
    return CleanupSummary(tuple(applied), tuple(failed), 0)


def _duplicate_raw_actions(root: Path) -> list[CleanupAction]:
    raw_root = root / "data" / "raw" / "taifex"
    if not raw_root.exists():
        return []
    hashes: dict[str, list[Path]] = {}
    for path in sorted(raw_root.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".csv", ".zip"}:
            hashes.setdefault(sha256_file(path), []).append(path)
    actions: list[CleanupAction] = []
    for paths in hashes.values():
        for duplicate in paths[1:]:
            actions.append(
                CleanupAction(
                    "quarantine",
                    duplicate,
                    f"duplicate raw content; canonical file is {paths[0]}",
                    duplicate.stat().st_size,
                    False,
                )
            )
    return actions


def _old_result_actions(root: Path, keep_latest: int) -> list[CleanupAction]:
    if keep_latest < 0:
        raise ValueError("keep_latest must be non-negative")
    results_root = root / "data" / "results" / "backtests"
    if not results_root.exists():
        return []
    actions: list[CleanupAction] = []
    for strategy_dir in sorted(path for path in results_root.iterdir() if path.is_dir()):
        runs = sorted(
            (path for path in strategy_dir.iterdir() if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for run_dir in runs[keep_latest:]:
            actions.append(
                CleanupAction(
                    "quarantine",
                    run_dir,
                    f"old result beyond keep_latest={keep_latest}",
                    _path_size(run_dir),
                    False,
                )
            )
    return actions


def _validate_cleanup_path(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path is outside repository: {resolved}") from exc
    if not relative.parts or relative.parts[0] in _FORBIDDEN_ROOT_NAMES:
        raise ValueError(f"path is not in an allowed runtime area: {resolved}")
    if relative.parts[0] not in {"data", "logs", "artifacts"}:
        raise ValueError(f"path is not in data/logs/artifacts: {resolved}")
    return resolved


def _plan(actions: list[CleanupAction]) -> CleanupPlan:
    ordered = tuple(sorted(actions, key=lambda action: str(action.path)))
    return CleanupPlan(
        actions=ordered,
        total_bytes=sum(action.size_bytes for action in ordered),
        safe_action_count=sum(action.safe_to_apply_automatically for action in ordered),
        confirmation_action_count=sum(not action.safe_to_apply_automatically for action in ordered),
    )


def _path_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(child.stat().st_size for child in path.rglob("*") if child.is_file())
