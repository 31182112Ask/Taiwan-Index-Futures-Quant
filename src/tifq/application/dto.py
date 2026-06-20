"""Framework-neutral application data transfer objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True)
class OperationStatus:
    operation: str
    state: Literal["pending", "running", "complete", "warning", "failed"]
    message: str = ""
    progress: float | None = None
    completed: int | None = None
    total: int | None = None
    elapsed_seconds: float | None = None
    eta_seconds: float | None = None


@dataclass(frozen=True)
class WorkflowStepDTO:
    number: int
    code: str
    name: str
    status: str
    marker: str
    enabled: bool
    blocking_reason: str | None = None


@dataclass(frozen=True)
class WorkflowStateDTO:
    steps: tuple[WorkflowStepDTO, ...]


@dataclass(frozen=True)
class EnvironmentReportDTO:
    status: str
    checked_at: str
    duration_seconds: float
    issues: tuple[dict[str, object], ...]
    safe_cleanup_count: int
    confirmation_cleanup_count: int
    active_operations: tuple[str, ...]
    healthy_files: int = 0


@dataclass(frozen=True)
class CleanupActionDTO:
    action_id: str
    action: str
    path: str
    reason: str
    size_bytes: int
    safe: bool


@dataclass(frozen=True)
class CleanupPlanDTO:
    actions: tuple[CleanupActionDTO, ...]
    total_bytes: int
    safe_action_count: int
    confirmation_action_count: int


@dataclass(frozen=True)
class CleanupResultDTO:
    applied: tuple[str, ...]
    failed: tuple[str, ...]
    bytes_reclaimed: int


@dataclass(frozen=True)
class DownloadPlanDTO:
    items: tuple[dict[str, object], ...]
    valid_existing_count: int
    missing_count: int
    conflict_count: int


@dataclass(frozen=True)
class PipelineResultDTO:
    operation: str
    changed: int
    skipped: int
    output_paths: tuple[str, ...]
    no_op: bool
    timings: dict[str, float] = field(default_factory=dict)
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PreflightDTO:
    fingerprint: dict[str, object]
    diagnostics: dict[str, object]
    bar_count: int
    signal_count: int
    trading_days: int
    contracts: tuple[str, ...]


@dataclass(frozen=True)
class PreparedBacktest:
    summary: PreflightDTO
    _core: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class BacktestRunDTO:
    run_id: str
    run_dir: str
    metrics: dict[str, object]
    diagnostics: dict[str, object]
    timings: dict[str, float]


@dataclass(frozen=True)
class ResultSummaryDTO:
    run_id: str
    strategy: str
    metrics: dict[str, object]
    legacy: bool
    artifact_status: dict[str, bool]
    run_dir: str


@dataclass(frozen=True)
class LoadedRunDTO:
    summary: ResultSummaryDTO
    config: dict[str, Any]
    metrics: dict[str, object]
    trades: tuple[dict[str, Any], ...]
    equity_curve: tuple[dict[str, Any], ...]
    model_bars_path: str | None
    signals: tuple[dict[str, Any], ...]
    contract_selection: tuple[dict[str, Any], ...]
    diagnostics: dict[str, Any]
    timings: dict[str, float]


@dataclass(frozen=True)
class ComparisonDTO:
    records: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class WorkflowExecutionDTO:
    step: WorkflowStepDTO
    state: WorkflowStateDTO
    result: object | None = None


@dataclass(frozen=True)
class SyncRequest:
    raw_dir: Path
    limit: int
    overwrite: bool = False


@dataclass(frozen=True)
class ImportRequest:
    raw_dir: Path
    processed_dir: Path
    symbol: str = "TMF"
    force: bool = False


@dataclass(frozen=True)
class BuildBarsRequest:
    processed_dir: Path
    symbol: str
    timeframe: Literal["1m", "5m"]
    force: bool = False


@dataclass(frozen=True)
class WorkflowOptions:
    sync_limit: int = 1
    overwrite: bool = False
    force: bool = False
