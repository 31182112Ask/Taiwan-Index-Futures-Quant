"""Environment and cleanup application service."""

from __future__ import annotations

from pathlib import Path

from tifq.application._progress import progress_callback
from tifq.application.dto import (
    CleanupActionDTO,
    CleanupPlanDTO,
    CleanupResultDTO,
    EnvironmentReportDTO,
)
from tifq.application.ports import ProgressSink
from tifq.runtime.cleanup import (
    CleanupAction,
    CleanupSummary,
    apply_confirmed_cleanup,
    apply_safe_cleanup,
    build_cleanup_plan,
)
from tifq.runtime.health import run_environment_health_check


class EnvironmentService:
    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()
        self._actions: dict[str, CleanupAction] = {}

    def check(
        self, *, full_scan: bool = False, progress_sink: ProgressSink | None = None
    ) -> EnvironmentReportDTO:
        report = run_environment_health_check(
            self.repository_root,
            full_scan=full_scan,
            progress_callback=progress_callback(progress_sink),
        )
        return EnvironmentReportDTO(
            status=report.status,
            checked_at=report.checked_at,
            duration_seconds=report.duration_seconds,
            issues=tuple(
                {
                    "code": issue.code,
                    "severity": issue.severity,
                    "path": str(issue.path) if issue.path else None,
                    "message": issue.message,
                    "recoverable": issue.recoverable,
                }
                for issue in report.issues
            ),
            safe_cleanup_count=report.cleanup_plan.safe_action_count,
            confirmation_cleanup_count=report.cleanup_plan.confirmation_action_count,
            active_operations=report.active_operations,
            healthy_files=report.healthy_files,
        )

    def build_cleanup_plan(
        self,
        *,
        full_scan: bool = False,
        prune_results: bool = False,
        keep_latest: int = 20,
    ) -> CleanupPlanDTO:
        plan = build_cleanup_plan(
            self.repository_root,
            full_scan=full_scan,
            prune_results=prune_results,
            keep_latest=keep_latest,
        )
        self._actions = {self._action_id(action): action for action in plan.actions}
        return CleanupPlanDTO(
            actions=tuple(
                CleanupActionDTO(
                    self._action_id(action),
                    action.action,
                    str(action.path),
                    action.reason,
                    action.size_bytes,
                    action.safe_to_apply_automatically,
                )
                for action in plan.actions
            ),
            total_bytes=plan.total_bytes,
            safe_action_count=plan.safe_action_count,
            confirmation_action_count=plan.confirmation_action_count,
        )

    def apply_safe_cleanup(self, progress_sink: ProgressSink | None = None) -> CleanupResultDTO:
        plan = build_cleanup_plan(self.repository_root)
        return self._cleanup_result(apply_safe_cleanup(plan, self.repository_root))

    def apply_confirmed_cleanup(
        self,
        action_ids: tuple[str, ...],
        progress_sink: ProgressSink | None = None,
    ) -> CleanupResultDTO:
        if not self._actions:
            self.build_cleanup_plan(full_scan=True)
        actions = tuple(self._actions[action_id] for action_id in action_ids)
        return self._cleanup_result(apply_confirmed_cleanup(actions, self.repository_root))

    @staticmethod
    def _action_id(action: CleanupAction) -> str:
        return f"{action.action}:{action.path.resolve()}"

    @staticmethod
    def _cleanup_result(summary: CleanupSummary) -> CleanupResultDTO:
        return CleanupResultDTO(
            tuple(str(action.path) for action in summary.applied),
            summary.failed,
            summary.bytes_reclaimed,
        )
