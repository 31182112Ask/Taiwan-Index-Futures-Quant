from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from click import unstyle
from typer.testing import CliRunner

from tifq.application.dto import DownloadPlanDTO
from tifq.cli import _workflow_cli_marker, app
from tifq.data.taifex_fetcher import (
    TaifexDownloadPlan,
    TaifexDownloadPlanItem,
    TaifexRemoteFile,
)

runner = CliRunner()


def make_runtime_dirs(root: Path) -> None:
    for directory in (
        root / "data" / "raw" / "taifex",
        root / "data" / "processed",
        root / "data" / "results" / "backtests",
        root / "logs",
    ):
        directory.mkdir(parents=True)


def test_doctor_returns_nonzero_for_corrupt_manifest(tmp_path: Path, monkeypatch) -> None:
    make_runtime_dirs(tmp_path)
    (tmp_path / "data" / "processed" / "import_manifest.json").write_text(
        "broken", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "corrupt_manifest" in result.stdout


def test_clean_is_dry_run_by_default_and_apply_safe_deletes_only_temp(
    tmp_path: Path,
    monkeypatch,
) -> None:
    make_runtime_dirs(tmp_path)
    stale = tmp_path / "data" / "raw" / "taifex" / "download.zip.part"
    raw = tmp_path / "data" / "raw" / "taifex" / "manual.csv"
    stale.write_bytes(b"temporary")
    raw.write_bytes(b"valuable")
    os.utime(stale, (1, 1))
    monkeypatch.chdir(tmp_path)

    dry_run = runner.invoke(app, ["clean"])
    assert stale.exists()
    applied = runner.invoke(app, ["clean", "--apply-safe"])

    assert dry_run.exit_code == 0
    assert stale.exists() is False
    assert raw.exists()
    assert "Cleanup plan (dry-run)" in dry_run.stdout
    assert applied.exit_code == 0


def test_sync_overwrite_requires_yes() -> None:
    result = runner.invoke(
        app,
        ["sync-taifex", "--overwrite", "--download-only"],
        terminal_width=200,
        color=False,
    )

    assert result.exit_code == 2
    assert "requires explicit --yes" in unstyle(result.stderr)


def test_sync_plan_does_not_call_download(monkeypatch, tmp_path: Path) -> None:
    remote = TaifexRemoteFile(
        date(2026, 6, 18),
        "https://www.taifex.com.tw/file/Daily_20260618.csv",
        "Daily_20260618.csv",
    )
    plan = TaifexDownloadPlan(
        (
            TaifexDownloadPlanItem(
                remote,
                tmp_path / "raw" / "Daily_20260618.csv",
                "new",
                None,
                None,
                "download_missing",
            ),
        )
    )
    dto = DownloadPlanDTO(
        (
            {
                "trading_date": remote.trading_date.isoformat(),
                "status": "new",
                "remote_filename": remote.remote_filename,
                "local_path": str(plan.items[0].local_path),
                "recommended_action": "download_missing",
            },
        ),
        0,
        1,
        0,
    )
    pipeline = SimpleNamespace(plan_sync=lambda request: dto)
    monkeypatch.setattr(
        "tifq.cli._application", lambda: SimpleNamespace(data_pipeline=pipeline)
    )

    result = runner.invoke(
        app,
        ["sync-taifex", "--raw-dir", str(tmp_path / "raw"), "--plan"],
    )

    assert result.exit_code == 0
    assert "TAIFEX download plan" in result.stdout
    assert not (tmp_path / "raw").exists()


def test_workflow_help_lists_stop_and_machine_readable_options() -> None:
    result = runner.invoke(app, ["workflow", "--help"], terminal_width=200, color=False)

    assert result.exit_code == 0
    output = unstyle(result.stdout)
    assert "--stop-after" in output
    assert "--quiet" in output
    assert "--json" in output


def test_workflow_markers_are_safe_on_windows_legacy_code_pages() -> None:
    markers = " ".join(
        _workflow_cli_marker(status) for status in ("complete", "warning", "running")
    )

    assert markers.encode("gbk")
    assert markers.encode("cp950")


def test_workflow_can_stop_after_doctor_with_json(tmp_path: Path, monkeypatch) -> None:
    config_path = Path(__file__).parents[2] / "configs" / "v1_backtest.yaml"
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        [
            "workflow",
            "--config",
            str(config_path),
            "--stop-after",
            "doctor",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert '"step": "doctor"' in result.stdout
    assert '"status": "complete"' in result.stdout
