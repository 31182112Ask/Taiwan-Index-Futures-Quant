from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from tifq import workflow
from tifq.backtest import BacktestPreflight
from tifq.config.models import BacktestConfig
from tifq.data.taifex_fetcher import (
    TaifexDownloadPlan,
    TaifexDownloadPlanItem,
    TaifexRemoteFile,
)
from tifq.runtime.cleanup import CleanupPlan
from tifq.runtime.health import HealthIssue, HealthReport
from tifq.runtime.manifests import sha256_file
from tifq.workflow import (
    WorkflowStepState,
    derive_workflow_state,
    discover_latest_matching_result,
    load_persisted_workflow_plan,
    persist_workflow_plan,
    raw_directory_fingerprint,
    validate_bar_state,
    validate_import_state,
    validate_preflight_state,
    validate_result_state,
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
                tmp_path
                / "data"
                / "raw"
                / "taifex"
                / "official"
                / "2026-06-18"
                / "Daily_20260618.zip",
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


def _write_valid_download(config: BacktestConfig, plan: TaifexDownloadPlan) -> None:
    item = plan.items[0]
    item.local_path.parent.mkdir(parents=True, exist_ok=True)
    item.local_path.write_bytes(b"official")
    manifest = [
        {
            "trading_date": item.remote.trading_date.isoformat(),
            "download_url": item.remote.download_url,
            "remote_filename": item.remote.remote_filename,
            "local_path": str(item.local_path),
            "size_bytes": item.local_path.stat().st_size,
            "sha256": sha256_file(item.local_path),
        }
    ]
    (config.data.raw_dir / "download_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


def test_partial_sync_failure_sets_warning(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.data.raw_dir.mkdir(parents=True)
    plan = _plan(tmp_path)

    state = derive_workflow_state(
        config,
        _health(),
        plan=plan,
        plan_raw_fingerprint=raw_directory_fingerprint(config.data.raw_dir),
    )

    assert state.steps[2].status == "warning"
    assert "sync incomplete" in str(state.steps[2].blocking_reason)


def test_partial_sync_failure_does_not_enable_import(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.data.raw_dir.mkdir(parents=True)
    plan = _plan(tmp_path)

    state = derive_workflow_state(
        config,
        _health(),
        plan=plan,
        plan_raw_fingerprint=raw_directory_fingerprint(config.data.raw_dir),
    )

    assert state.steps[3].enabled is False


def test_zero_failure_sync_sets_complete(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.data.raw_dir.mkdir(parents=True)
    plan = _plan(tmp_path)
    _write_valid_download(config, plan)
    refreshed = workflow.build_taifex_download_plan(
        config.data.raw_dir, [item.remote for item in plan.items]
    )

    state = derive_workflow_state(
        config,
        _health(),
        plan=refreshed,
        plan_raw_fingerprint=raw_directory_fingerprint(config.data.raw_dir),
    )

    assert state.steps[2].status == "complete"
    assert state.steps[3].enabled is True


def test_restart_restores_sync_complete_from_manifest(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.data.raw_dir.mkdir(parents=True)
    plan = _plan(tmp_path)
    _write_valid_download(config, plan)
    refreshed = workflow.build_taifex_download_plan(
        config.data.raw_dir, [item.remote for item in plan.items]
    )
    persist_workflow_plan(config, refreshed, requested_limit=1)

    restored, fingerprint = load_persisted_workflow_plan(config)
    state = derive_workflow_state(
        config,
        _health(),
        plan=restored,
        plan_raw_fingerprint=fingerprint,
    )

    assert restored is not None
    assert state.steps[2].status == "complete"


def test_persisted_plan_invalidates_when_raw_fingerprint_changes(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.data.raw_dir.mkdir(parents=True)
    persist_workflow_plan(config, _plan(tmp_path), requested_limit=1)
    (config.data.raw_dir / "manual.csv").write_text("changed", encoding="utf-8")

    restored, fingerprint = load_persisted_workflow_plan(config)

    assert restored is None
    assert fingerprint is None


def test_restart_restores_import_complete_from_hashes(tmp_path: Path) -> None:
    config = _config(tmp_path)
    source = config.data.raw_dir / "manual.csv"
    output = config.data.processed_dir / "ticks" / "TMF" / "2026-06-18.parquet"
    source.parent.mkdir(parents=True)
    output.parent.mkdir(parents=True)
    source.write_text("raw", encoding="utf-8")
    output.write_bytes(b"ticks")
    manifest = {
        "parser_version": "v1",
        "records": [
            {
                "source_path": str(source),
                "sha256": sha256_file(source),
                "parser_version": "v1",
                "output_paths": [str(output)],
                "output_hashes": {str(output): sha256_file(output)},
            }
        ],
    }
    (config.data.processed_dir / "import_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    assert validate_import_state(config).complete


def test_restart_restores_bar_complete_from_hashes(tmp_path: Path) -> None:
    config = _config(tmp_path)
    tick = config.data.processed_dir / "ticks" / "TMF" / "2026-06-18.parquet"
    bar = config.data.processed_dir / "bars" / "TMF" / "5m" / "2026-06-18.parquet"
    tick.parent.mkdir(parents=True)
    bar.parent.mkdir(parents=True)
    tick.write_bytes(b"ticks")
    bar.write_bytes(b"bars")
    manifest = {
        "builder_version": "v1",
        "records": [
            {
                "timeframe": "5m",
                "tick_path": str(tick),
                "tick_hash": sha256_file(tick),
                "builder_version": "v1",
                "output_paths": [str(bar)],
                "output_hashes": {str(bar): sha256_file(bar)},
            }
        ],
    }
    (config.data.processed_dir / "bar_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    assert validate_bar_state(config).complete


def _write_result_run(config: BacktestConfig, fingerprint: dict[str, object]) -> Path:
    run = config.data.processed_dir.parent / "results" / "backtests" / "vwap_trend" / "run"
    run.mkdir(parents=True)
    (run / "config.yaml").write_text("project: test\n", encoding="utf-8")
    pd.DataFrame({"entry_time": []}).to_csv(run / "trades.csv", index=False)
    pd.DataFrame({"timestamp": [], "equity": []}).to_csv(
        run / "equity_curve.csv", index=False
    )
    (run / "metrics.json").write_text('{"trade_count": 0}', encoding="utf-8")
    pd.DataFrame({"timestamp": ["2026-06-18"], "close": [1]}).to_parquet(
        run / "model_bars.parquet", index=False
    )
    (run / "signals.csv").write_text("timestamp,side\n", encoding="utf-8")
    (run / "contract_selection.csv").write_text("trading_date,contract\n", encoding="utf-8")
    (run / "diagnostics.json").write_text('{"status": "healthy"}', encoding="utf-8")
    (run / "timings.json").write_text('{"total": 1}', encoding="utf-8")
    (run / "data_fingerprint.json").write_text(json.dumps(fingerprint), encoding="utf-8")
    return run


def test_restart_discovers_latest_matching_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    fingerprint = {"current": True}
    run = _write_result_run(config, fingerprint)
    monkeypatch.setattr(workflow, "build_data_fingerprint", lambda _config: fingerprint)

    assert discover_latest_matching_result(config) == run


def test_stale_result_fingerprint_is_not_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    run = _write_result_run(config, {"stale": True})
    monkeypatch.setattr(workflow, "build_data_fingerprint", lambda _config: {"current": True})

    assert not validate_result_state(config, run).complete


def test_corrupt_result_artifact_removes_completion_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    fingerprint = {"current": True}
    run = _write_result_run(config, fingerprint)
    (run / "model_bars.parquet").write_bytes(b"corrupt")
    monkeypatch.setattr(workflow, "build_data_fingerprint", lambda _config: fingerprint)

    assert not validate_result_state(config, run).complete
