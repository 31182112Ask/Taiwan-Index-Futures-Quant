from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from tifq.backtest import BacktestPreflight
from tifq.config.models import BacktestConfig
from tifq.data.taifex_fetcher import (
    TaifexDownloadPlan,
    TaifexDownloadPlanItem,
    TaifexRemoteFile,
)
from tifq.runtime.cleanup import CleanupPlan
from tifq.runtime.health import HealthIssue, HealthReport
from tifq.workflow import (
    WorkflowStepState,
    derive_workflow_state,
    raw_directory_fingerprint,
    validate_preflight_state,
)


def _config(tmp_path: Path) -> BacktestConfig:
    return BacktestConfig.model_validate(
        {
            "project": {"name": "test", "timezone": "Asia/Taipei"},
            "data": {
                "symbol": "TMF",
                "contract_mode": "continuous_front_month",
                "raw_dir": tmp_path / "data" / "raw" / "taifex",
                "processed_dir": tmp_path / "data" / "processed",
                "start_date": date(2026, 6, 17),
                "end_date": date(2026, 6, 18),
                "session": "day",
                "timeframe": "5m",
            },
            "cost": {
                "commission_per_side": 5,
                "tax_rate": 0.00002,
                "slippage_points_per_side": 1,
            },
            "strategy": {"name": "vwap_trend", "params": {}},
            "portfolio": {"initial_cash": 100_000, "max_position": 1},
        }
    )


def _health(*issues: HealthIssue) -> HealthReport:
    status = (
        "error"
        if any(issue.severity == "error" for issue in issues)
        else ("warning" if issues else "healthy")
    )
    return HealthReport(
        status=status,
        checked_at="2026-06-19T00:00:00+00:00",
        duration_seconds=0.01,
        healthy_files=7,
        issues=issues,
        cleanup_plan=CleanupPlan((), 0, 0, 0),
        active_operations=(),
    )


def _plan(tmp_path: Path) -> TaifexDownloadPlan:
    remote = TaifexRemoteFile(
        date(2026, 6, 18),
        "https://www.taifex.com.tw/file/Daily_20260618.zip",
        "Daily_20260618.zip",
    )
    return TaifexDownloadPlan(
        (
            TaifexDownloadPlanItem(
                remote,
                tmp_path / "data" / "raw" / "taifex" / "Daily_20260618.zip",
                "new",
                None,
                None,
                "download_missing",
            ),
        )
    )


def test_workflow_markers_are_dynamic() -> None:
    assert WorkflowStepState(1, "Check", "complete", True, None).marker == "✅"
    assert WorkflowStepState(1, "Check", "warning", True, "blocked").marker == "⚠"
    assert WorkflowStepState(1, "Check", "running", True, None).marker == "…"
    assert WorkflowStepState(1, "Check", "pending", True, None).marker == ""


def test_next_step_is_disabled_until_previous_step_is_complete(tmp_path: Path) -> None:
    state = derive_workflow_state(_config(tmp_path), _health())

    assert state.steps[0].status == "complete"
    assert state.steps[1].enabled is True
    assert state.steps[2].enabled is False
    assert state.steps[3].status == "pending"
    assert state.steps[4].enabled is False


def test_successful_current_plan_enables_sync(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.data.raw_dir.mkdir(parents=True)
    plan = _plan(tmp_path)

    state = derive_workflow_state(
        config,
        _health(),
        plan=plan,
        plan_raw_fingerprint=raw_directory_fingerprint(config.data.raw_dir),
    )

    assert state.steps[1].status == "complete"
    assert state.steps[2].enabled is True


def test_warning_marker_does_not_hard_block_recoverable_next_step(tmp_path: Path) -> None:
    issue = HealthIssue("stale_temp", "warning", None, "cleanup available", True)

    state = derive_workflow_state(_config(tmp_path), _health(issue))

    assert state.steps[0].status == "warning"
    assert state.steps[1].enabled is True


def test_preflight_fingerprint_staleness_is_blocking(tmp_path: Path) -> None:
    config = _config(tmp_path)
    stale = BacktestPreflight(
        model_bars=pd.DataFrame(),
        signals=pd.DataFrame(),
        contract_selection=pd.DataFrame(),
        diagnostics={"errors": []},
        timings={},
        data_fingerprint={"stale": True},
    )

    check = validate_preflight_state(config, stale)

    assert check.complete is False
    assert check.blocking_reason == "preflight fingerprint is stale"
