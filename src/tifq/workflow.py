"""Deterministic V1 linear workflow state and artifact validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from tifq.backtest import BacktestPreflight, build_data_fingerprint
from tifq.config.models import BacktestConfig
from tifq.data.taifex_fetcher import (
    TaifexDownloadPlan,
    TaifexRemoteFile,
    build_taifex_download_plan,
)
from tifq.runtime.health import HealthReport
from tifq.runtime.manifests import sha256_file

WorkflowStatus = Literal["pending", "complete", "warning", "running"]
WORKFLOW_STEP_NAMES = (
    "Check environment",
    "Plan data",
    "Sync data",
    "Import data",
    "Build bars",
    "Backtest preflight",
    "Run backtest",
    "View results",
)
WORKFLOW_BUTTON_NAMES = (
    "Check",
    "Plan",
    "Sync",
    "Import",
    "Bars",
    "Preflight",
    "Backtest",
    "Results",
)
REQUIRED_RESULT_ARTIFACTS = (
    "config.yaml",
    "trades.csv",
    "equity_curve.csv",
    "metrics.json",
    "model_bars.parquet",
    "signals.csv",
    "contract_selection.csv",
    "diagnostics.json",
    "timings.json",
    "data_fingerprint.json",
)
WORKFLOW_STATE_SCHEMA_VERSION = 1
PLAN_MAX_AGE = timedelta(hours=24)


@dataclass(frozen=True)
class WorkflowCheck:
    """One factual workflow completion check."""

    complete: bool
    warning: str | None = None
    blocking_reason: str | None = None


@dataclass(frozen=True)
class WorkflowStepState:
    """Rendered and actionable state for one ordered workflow step."""

    number: int
    name: str
    status: WorkflowStatus
    enabled: bool
    blocking_reason: str | None

    @property
    def marker(self) -> str:
        """Return the required visual status marker."""
        return {
            "complete": "✅",
            "warning": "⚠",
            "running": "…",
            "pending": "",
        }[self.status]

    @property
    def label(self) -> str:
        """Return compact button text with a dynamically derived marker."""
        marker = f" {self.marker}" if self.marker else ""
        return f"{self.number} {WORKFLOW_BUTTON_NAMES[self.number - 1]}{marker}"


@dataclass(frozen=True)
class WorkflowState:
    """Complete eight-step state used by both CLI and Streamlit."""

    steps: tuple[WorkflowStepState, ...]


def derive_workflow_state(
    config: BacktestConfig,
    health: HealthReport,
    *,
    plan: TaifexDownloadPlan | None = None,
    plan_raw_fingerprint: tuple[tuple[str, int, int], ...] | None = None,
    preflight: BacktestPreflight | None = None,
    latest_run_dir: str | Path | None = None,
    running_step: int | None = None,
) -> WorkflowState:
    """Derive all markers and enabled states from current files and session artifacts."""
    current_fingerprint = (
        build_data_fingerprint(config)
        if preflight is not None or latest_run_dir is not None
        else None
    )
    result_check = validate_result_state(
        config,
        latest_run_dir,
        current_fingerprint=current_fingerprint,
    )
    import_check = validate_import_state(config, plan) if plan is not None else WorkflowCheck(False)
    bar_check = validate_bar_state(config) if import_check.complete else WorkflowCheck(False)
    checks = (
        _health_check(health),
        _plan_check(config, plan, plan_raw_fingerprint),
        validate_sync_state(config, plan),
        import_check,
        bar_check,
        validate_preflight_state(
            config,
            preflight,
            current_fingerprint=current_fingerprint,
        ),
        result_check,
        result_check,
    )
    steps: list[WorkflowStepState] = []
    previous_allows_next = True
    for number, (name, check) in enumerate(zip(WORKFLOW_STEP_NAMES, checks, strict=True), start=1):
        enabled = number == 1 or previous_allows_next
        if running_step == number:
            status: WorkflowStatus = "running"
        elif check.blocking_reason or check.warning:
            status = "warning"
        elif check.complete:
            status = "complete"
        else:
            status = "pending"
        steps.append(
            WorkflowStepState(
                number,
                name,
                status,
                enabled,
                check.blocking_reason or check.warning,
            )
        )
        previous_allows_next = (
            previous_allows_next and check.complete and check.blocking_reason is None
        )
    return WorkflowState(tuple(steps))


def raw_directory_fingerprint(raw_dir: str | Path) -> tuple[tuple[str, int, int], ...]:
    """Return a stable inexpensive raw-directory fingerprint for plan invalidation."""
    root = Path(raw_dir)
    if not root.exists():
        return ()
    return tuple(
        (str(path.resolve()), path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix.lower() in {".csv", ".zip", ".json"}
    )


def persist_workflow_plan(
    config: BacktestConfig,
    plan: TaifexDownloadPlan,
    *,
    requested_limit: int,
) -> Path:
    """Persist a verifiable download-plan index for restart recovery."""
    state_path = _workflow_state_path(config)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": WORKFLOW_STATE_SCHEMA_VERSION,
        "created_at": datetime.now(tz=UTC).isoformat(),
        "requested_limit": requested_limit,
        "context": _plan_context(config),
        "raw_directory_fingerprint": [
            list(item) for item in raw_directory_fingerprint(config.data.raw_dir)
        ],
        "items": [
            {
                "trading_date": item.remote.trading_date.isoformat(),
                "download_url": item.remote.download_url,
                "remote_filename": item.remote.remote_filename,
                "local_path": str(item.local_path.resolve()),
            }
            for item in plan.items
        ],
    }
    temporary = state_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(state_path)
    return state_path


def load_persisted_workflow_plan(
    config: BacktestConfig,
    *,
    now: datetime | None = None,
) -> tuple[TaifexDownloadPlan | None, tuple[tuple[str, int, int], ...] | None]:
    """Restore a current plan only when context, age, and raw fingerprint match."""
    payload = _json_object(_workflow_state_path(config))
    if payload.get("schema_version") != WORKFLOW_STATE_SCHEMA_VERSION:
        return None, None
    if payload.get("context") != _plan_context(config):
        return None, None
    try:
        created_at = datetime.fromisoformat(str(payload["created_at"]))
    except (KeyError, ValueError):
        return None, None
    current_time = now or datetime.now(tz=UTC)
    if created_at.tzinfo is None or current_time - created_at > PLAN_MAX_AGE:
        return None, None
    stored_fingerprint = _decode_raw_fingerprint(payload.get("raw_directory_fingerprint"))
    current_fingerprint = raw_directory_fingerprint(config.data.raw_dir)
    if stored_fingerprint is None or stored_fingerprint != current_fingerprint:
        return None, None
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return None, None
    try:
        remotes = [
            TaifexRemoteFile(
                trading_date=datetime.fromisoformat(str(item["trading_date"])).date(),
                download_url=str(item["download_url"]),
                remote_filename=str(item["remote_filename"]),
            )
            for item in raw_items
            if isinstance(item, dict)
        ]
    except (KeyError, ValueError):
        return None, None
    if len(remotes) != len(raw_items):
        return None, None
    plan = build_taifex_download_plan(config.data.raw_dir, remotes)
    expected_paths = {str(Path(str(item["local_path"])).resolve()) for item in raw_items}
    if {str(item.local_path.resolve()) for item in plan.items} != expected_paths:
        return None, None
    return plan, current_fingerprint


def discover_latest_matching_result(config: BacktestConfig) -> Path | None:
    """Find the newest complete result whose fingerprint matches current config/data."""
    root = config.data.processed_dir.parent / "results" / "backtests"
    if not root.is_dir():
        return None
    candidates = sorted(
        (path for path in root.glob("*/*") if path.is_dir()),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    current_fingerprint = build_data_fingerprint(config)
    return next(
        (
            path
            for path in candidates
            if validate_result_state(
                config,
                path,
                current_fingerprint=current_fingerprint,
            ).complete
        ),
        None,
    )


def validate_import_state(
    config: BacktestConfig,
    plan: TaifexDownloadPlan | None = None,
) -> WorkflowCheck:
    """Validate parser/source/output fingerprints for selected raw sources."""
    manifest = _json_object(config.data.processed_dir / "import_manifest.json")
    records = manifest.get("records") if manifest else None
    if not isinstance(records, list):
        return WorkflowCheck(False)
    selected = {str(item.local_path.resolve()) for item in plan.items} if plan is not None else None
    matched = 0
    for raw_record in records:
        if not isinstance(raw_record, dict):
            return WorkflowCheck(False, blocking_reason="invalid import manifest record")
        source = Path(str(raw_record.get("source_path", "")))
        if selected is not None and str(source.resolve()) not in selected:
            continue
        matched += 1
        if not source.exists() or sha256_file(source) != raw_record.get("sha256"):
            return WorkflowCheck(False, blocking_reason="raw source fingerprint mismatch")
        if raw_record.get("parser_version") != manifest.get("parser_version"):
            return WorkflowCheck(False, blocking_reason="parser version mismatch")
        if not _record_outputs_valid(raw_record):
            return WorkflowCheck(False, blocking_reason="import output hash mismatch")
    required = len(selected) if selected is not None else len(records)
    return WorkflowCheck(required > 0 and matched == required)


def validate_sync_state(
    config: BacktestConfig,
    plan: TaifexDownloadPlan | None,
) -> WorkflowCheck:
    """Validate selected official files against the current download manifest and hashes."""
    if plan is None:
        return WorkflowCheck(False)
    current = build_taifex_download_plan(config.data.raw_dir, [item.remote for item in plan.items])
    invalid = [item for item in current.items if item.status != "valid_existing"]
    if not current.items or invalid:
        details = ", ".join(
            f"{item.remote.trading_date.isoformat()}={item.status}" for item in invalid
        )
        return WorkflowCheck(
            False,
            blocking_reason=f"sync incomplete: {len(invalid)} selected file(s) invalid ({details})",
        )
    return WorkflowCheck(True)


def validate_bar_state(config: BacktestConfig) -> WorkflowCheck:
    """Validate builder/tick/output fingerprints and configured bar availability."""
    manifest = _json_object(config.data.processed_dir / "bar_manifest.json")
    records = manifest.get("records") if manifest else None
    if not isinstance(records, list):
        return WorkflowCheck(False)
    relevant = [
        record
        for record in records
        if isinstance(record, dict) and record.get("timeframe") == config.data.timeframe
    ]
    if not relevant:
        return WorkflowCheck(False)
    tick_dir = config.data.processed_dir / "ticks" / config.data.symbol
    tick_paths = (
        {str(path.resolve()) for path in tick_dir.glob("*.parquet") if path.is_file()}
        if tick_dir.exists()
        else set()
    )
    recorded_tick_paths = {
        str(Path(str(record.get("tick_path", ""))).resolve()) for record in relevant
    }
    if not tick_paths or tick_paths != recorded_tick_paths:
        return WorkflowCheck(False, blocking_reason="bar manifest does not cover all tick files")
    for record in relevant:
        tick = Path(str(record.get("tick_path", "")))
        if not tick.exists() or sha256_file(tick) != record.get("tick_hash"):
            return WorkflowCheck(False, blocking_reason="tick source fingerprint mismatch")
        if record.get("builder_version") != manifest.get("builder_version"):
            return WorkflowCheck(False, blocking_reason="builder version mismatch")
        if not _record_outputs_valid(record):
            return WorkflowCheck(False, blocking_reason="bar output hash mismatch")
    bar_dir = config.data.processed_dir / "bars" / config.data.symbol / config.data.timeframe
    has_range = (
        any(
            path.is_file()
            and path.suffix == ".parquet"
            and config.data.start_date.isoformat() <= path.stem <= config.data.end_date.isoformat()
            for path in bar_dir.glob("*.parquet")
        )
        if bar_dir.exists()
        else False
    )
    return WorkflowCheck(has_range)


def validate_preflight_state(
    config: BacktestConfig,
    preflight: BacktestPreflight | None,
    *,
    current_fingerprint: dict[str, Any] | None = None,
) -> WorkflowCheck:
    """Require a non-blocking preflight bound to the exact current fingerprint."""
    if preflight is None:
        return WorkflowCheck(False)
    fingerprint = current_fingerprint or build_data_fingerprint(config)
    if preflight.data_fingerprint != fingerprint:
        return WorkflowCheck(False, blocking_reason="preflight fingerprint is stale")
    errors = preflight.diagnostics.get("errors", [])
    return WorkflowCheck(not errors, blocking_reason="; ".join(errors) if errors else None)


def validate_result_state(
    config: BacktestConfig,
    run_dir: str | Path | None,
    *,
    current_fingerprint: dict[str, Any] | None = None,
) -> WorkflowCheck:
    """Require a complete published run bound to the current data fingerprint."""
    if run_dir is None:
        return WorkflowCheck(False)
    root = Path(run_dir)
    if not root.is_dir() or any(not (root / name).exists() for name in REQUIRED_RESULT_ARTIFACTS):
        return WorkflowCheck(False, blocking_reason="result artifacts are incomplete")
    fingerprint = _json_object(root / "data_fingerprint.json")
    fingerprint_now = current_fingerprint or build_data_fingerprint(config)
    readable = _result_artifacts_readable(root)
    return WorkflowCheck(
        fingerprint == fingerprint_now and readable,
        blocking_reason=(
            "result fingerprint is stale"
            if fingerprint != fingerprint_now
            else (None if readable else "result artifacts are corrupt or unreadable")
        ),
    )


def _health_check(health: HealthReport) -> WorkflowCheck:
    errors = [issue.message for issue in health.issues if issue.severity == "error"]
    active = list(health.active_operations)
    blocking = errors + (["active data operation"] if active else [])
    warnings = [issue.message for issue in health.issues if issue.severity == "warning"]
    return WorkflowCheck(
        not blocking,
        warning="; ".join(warnings) if warnings else None,
        blocking_reason="; ".join(blocking) if blocking else None,
    )


def _plan_check(
    config: BacktestConfig,
    plan: TaifexDownloadPlan | None,
    plan_raw_fingerprint: tuple[tuple[str, int, int], ...] | None,
) -> WorkflowCheck:
    if plan is None or plan_raw_fingerprint is None:
        return WorkflowCheck(False)
    if not plan.items:
        return WorkflowCheck(False, blocking_reason="download plan is empty")
    if plan_raw_fingerprint != raw_directory_fingerprint(config.data.raw_dir):
        return WorkflowCheck(False, blocking_reason="download plan is stale")
    if plan.conflict_count:
        return WorkflowCheck(False, blocking_reason="download plan has local conflicts")
    return WorkflowCheck(True)


def _record_outputs_valid(record: dict[str, Any]) -> bool:
    paths = record.get("output_paths")
    hashes = record.get("output_hashes")
    if not isinstance(paths, list) or not isinstance(hashes, dict):
        return False
    return all(
        Path(str(path)).exists()
        and isinstance(hashes.get(str(path)), str)
        and sha256_file(str(path)) == hashes[str(path)]
        for path in paths
    )


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _workflow_state_path(config: BacktestConfig) -> Path:
    return config.data.processed_dir.parent / ".runtime" / "workflow_state.json"


def _plan_context(config: BacktestConfig) -> dict[str, str]:
    return {
        "raw_dir": str(config.data.raw_dir.resolve()),
        "symbol": config.data.symbol,
        "session": config.data.session,
    }


def _decode_raw_fingerprint(value: object) -> tuple[tuple[str, int, int], ...] | None:
    if not isinstance(value, list):
        return None
    try:
        return tuple((str(item[0]), int(item[1]), int(item[2])) for item in value)
    except (IndexError, TypeError, ValueError):
        return None


def _result_artifacts_readable(root: Path) -> bool:
    try:
        metrics = _json_object(root / "metrics.json")
        diagnostics = _json_object(root / "diagnostics.json")
        pd.read_csv(root / "trades.csv")
        pd.read_csv(root / "equity_curve.csv")
        pd.read_parquet(root / "model_bars.parquet")
    except (OSError, ValueError):
        return False
    return bool(metrics) and bool(diagnostics)
