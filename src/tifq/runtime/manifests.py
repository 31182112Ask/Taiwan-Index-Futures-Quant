"""Atomic JSON manifests and reusable file fingerprints."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

IMPORT_MANIFEST_FILENAME = "import_manifest.json"
BAR_MANIFEST_FILENAME = "bar_manifest.json"


@dataclass(frozen=True)
class FileFingerprint:
    """Stable content fingerprint with inexpensive metadata for cache reuse."""

    size: int
    mtime_ns: int
    sha256: str


def sha256_file(path: str | Path) -> str:
    """Hash one file without loading it all into memory."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_file(
    path: str | Path,
    previous: dict[str, Any] | None = None,
) -> FileFingerprint:
    """Reuse a prior hash only when size and nanosecond mtime still match."""
    file_path = Path(path)
    stat = file_path.stat()
    if previous is not None:
        previous_sha = previous.get("sha256") or previous.get("tick_hash")
        if (
            previous.get("size") == stat.st_size
            and previous.get("mtime_ns") == stat.st_mtime_ns
            and isinstance(previous_sha, str)
        ):
            return FileFingerprint(stat.st_size, stat.st_mtime_ns, previous_sha)
    return FileFingerprint(stat.st_size, stat.st_mtime_ns, sha256_file(file_path))


def load_json_manifest(
    path: str | Path,
    *,
    default: Any,
    quarantine_dir: str | Path | None = None,
) -> Any:
    """Load JSON or quarantine a corrupt manifest before returning a clean default."""
    manifest_path = Path(path)
    if not manifest_path.exists():
        return default
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        if quarantine_dir is None:
            raise
        quarantine_root = Path(quarantine_dir)
        quarantine_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%fZ")
        destination = quarantine_root / f"{manifest_path.name}.{timestamp}.corrupt"
        manifest_path.replace(destination)
        return default


def atomic_write_json(path: str | Path, payload: Any) -> None:
    """Atomically replace a JSON file using a sibling .part file."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = output_path.with_name(output_path.name + ".part")
    try:
        part_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        part_path.replace(output_path)
    except Exception:
        part_path.unlink(missing_ok=True)
        raise
