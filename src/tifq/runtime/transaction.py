"""Transaction-like staging and rollback for processed data publications."""

from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4


class StagedPublication:
    """Stage complete files, publish atomically per file, and rollback as a set."""

    def __init__(self, processed_dir: str | Path, operation: str) -> None:
        self.processed_dir = Path(processed_dir).resolve()
        self.staging_dir = self.processed_dir / ".staging" / f"{operation}-{uuid4().hex}"
        self.outputs_dir = self.staging_dir / "outputs"
        self.backups_dir = self.staging_dir / "backups"
        self.staging_dir.mkdir(parents=True, exist_ok=False)
        self._targets: dict[Path, Path | None] = {}

    def stage_path(self, target: str | Path) -> Path:
        """Return the staging path corresponding to one processed output target."""
        resolved = Path(target).resolve()
        relative = resolved.relative_to(self.processed_dir)
        staged = self.outputs_dir / relative
        staged.parent.mkdir(parents=True, exist_ok=True)
        self._targets[resolved] = staged
        return staged

    def stage_delete(self, target: str | Path) -> None:
        """Stage deletion of a generated output as part of the same publication."""
        resolved = Path(target).resolve()
        resolved.relative_to(self.processed_dir)
        self._targets[resolved] = None

    def publish(self, manifest_target: str | Path, manifest_staged: str | Path) -> None:
        """Publish all staged outputs and the manifest last, restoring on any failure."""
        manifest = Path(manifest_target).resolve()
        staged_manifest = Path(manifest_staged)
        published: list[Path] = []
        manifest_existed = manifest.exists()
        try:
            for target in self._targets:
                self._backup(target)
            self._backup(manifest)
            for target, staged in self._targets.items():
                if staged is None:
                    target.unlink(missing_ok=True)
                    published.append(target)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                part = target.with_name(target.name + ".publish.part")
                shutil.copy2(staged, part)
                part.replace(target)
                published.append(target)
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest_part = manifest.with_name(manifest.name + ".publish.part")
            shutil.copy2(staged_manifest, manifest_part)
            manifest_part.replace(manifest)
        except Exception:
            for target in reversed(published):
                self._restore(target)
            if manifest_existed:
                self._restore(manifest)
            else:
                manifest.unlink(missing_ok=True)
            raise
        else:
            shutil.rmtree(self.staging_dir)

    def discard(self) -> None:
        """Remove an unused staging tree before publish."""
        shutil.rmtree(self.staging_dir, ignore_errors=True)

    def _backup(self, target: Path) -> None:
        if not target.exists():
            return
        backup = self.backups_dir / target.relative_to(self.processed_dir)
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup)

    def _restore(self, target: Path) -> None:
        backup = self.backups_dir / target.relative_to(self.processed_dir)
        if backup.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, target)
        else:
            target.unlink(missing_ok=True)
