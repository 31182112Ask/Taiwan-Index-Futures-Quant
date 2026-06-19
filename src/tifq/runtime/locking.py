"""Small cross-platform operation lock backed by an atomic lock file."""

from __future__ import annotations

import json
import os
import re
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

_SAFE_OPERATION_RE = re.compile(r"[^A-Za-z0-9_-]+")


class OperationLockError(RuntimeError):
    """Raised when a live process already owns an operation lock."""

    def __init__(self, message: str, info: OperationLockInfo | None = None) -> None:
        super().__init__(message)
        self.info = info


@dataclass(frozen=True)
class OperationLockInfo:
    """Serializable owner information shown by CLI and UI."""

    operation: str
    pid: int
    started_at: str
    path: Path


class OperationLock:
    """Acquire exactly one writer for a named local operation."""

    def __init__(
        self,
        lock_dir: str | Path,
        operation: str,
        *,
        lock_name: str | None = None,
    ) -> None:
        safe_operation = _SAFE_OPERATION_RE.sub("_", operation).strip("_")
        safe_lock_name = _SAFE_OPERATION_RE.sub("_", lock_name or operation).strip("_")
        if not safe_operation:
            raise ValueError("operation must contain at least one safe character")
        if not safe_lock_name:
            raise ValueError("lock_name must contain at least one safe character")
        self.operation = safe_operation
        self.lock_dir = Path(lock_dir)
        self.path = self.lock_dir / f"{safe_lock_name}.lock"
        self.info: OperationLockInfo | None = None

    def acquire(self) -> OperationLockInfo:
        """Acquire atomically, recovering a lock owned by a dead PID."""
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            existing = read_operation_lock(self.path)
            if existing is not None and _pid_is_active(existing.pid):
                raise OperationLockError(
                    f"operation '{existing.operation}' is already running "
                    f"under PID {existing.pid} since {existing.started_at}",
                    existing,
                )
            self.path.unlink(missing_ok=True)

        payload = {
            "operation": self.operation,
            "pid": os.getpid(),
            "started_at": datetime.now(tz=UTC).isoformat(),
            "path": str(self.path),
        }
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise OperationLockError(f"operation lock appeared concurrently: {self.path}") from exc
        try:
            os.write(descriptor, json.dumps(payload, sort_keys=True).encode("utf-8"))
        finally:
            os.close(descriptor)
        self.info = OperationLockInfo(
            operation=self.operation,
            pid=os.getpid(),
            started_at=str(payload["started_at"]),
            path=self.path,
        )
        return self.info

    def release(self) -> None:
        """Release only a lock still owned by this process."""
        existing = read_operation_lock(self.path)
        if existing is not None and existing.pid == os.getpid():
            self.path.unlink(missing_ok=True)
        self.info = None

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()


class PipelineOperationLock(AbstractContextManager["PipelineOperationLock"]):
    """Acquire the shared data writer lock before an operation-specific lock."""

    def __init__(self, lock_dir: str | Path, operation: str) -> None:
        self.shared = OperationLock(lock_dir, operation, lock_name="data_pipeline")
        self.specific = OperationLock(lock_dir, operation)

    def __enter__(self) -> PipelineOperationLock:
        self.shared.acquire()
        try:
            self.specific.acquire()
        except Exception:
            self.shared.release()
            raise
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.specific.release()
        self.shared.release()


def format_lock_conflict(error: OperationLockError) -> str:
    """Return a stable user-facing conflict message for CLI and Streamlit."""
    info = error.info
    if info is None:
        return f"Another data operation is running. {error} Wait for it to finish and retry."
    return (
        "Another data operation is running:\n"
        f"Operation: {info.operation}\n"
        f"PID: {info.pid}\n"
        f"Started: {info.started_at}\n"
        "Recommendation: wait for completion, then retry."
    )


def read_operation_lock(path: str | Path) -> OperationLockInfo | None:
    """Read a lock; malformed lock files are treated as stale."""
    lock_path = Path(path)
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        return OperationLockInfo(
            operation=str(payload["operation"]),
            pid=int(payload["pid"]),
            started_at=str(payload["started_at"]),
            path=lock_path,
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def active_operation_locks(lock_dir: str | Path) -> tuple[OperationLockInfo, ...]:
    """Return only locks whose owning process is still active."""
    root = Path(lock_dir)
    if not root.exists():
        return ()
    active: list[OperationLockInfo] = []
    for path in sorted(root.glob("*.lock")):
        info = read_operation_lock(path)
        if info is not None and _pid_is_active(info.pid):
            active.append(info)
    return tuple(active)


def remove_stale_operation_locks(lock_dir: str | Path) -> tuple[Path, ...]:
    """Remove malformed or dead-PID locks, never active locks."""
    root = Path(lock_dir)
    if not root.exists():
        return ()
    removed: list[Path] = []
    for path in sorted(root.glob("*.lock")):
        info = read_operation_lock(path)
        if info is None or not _pid_is_active(info.pid):
            path.unlink(missing_ok=True)
            removed.append(path)
    return tuple(removed)


def operation_lock_is_active(path: str | Path) -> bool:
    """Return whether a lock is well-formed and owned by a live process."""
    info = read_operation_lock(path)
    return info is not None and _pid_is_active(info.pid)


def _pid_is_active(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
