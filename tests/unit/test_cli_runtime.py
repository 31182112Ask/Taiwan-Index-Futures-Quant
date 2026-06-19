from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from typer.testing import CliRunner

from tifq.cli import app
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
    result = runner.invoke(app, ["sync-taifex", "--overwrite", "--download-only"])

    assert result.exit_code == 2
    assert "requires explicit --yes" in result.stderr


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
    monkeypatch.setattr("tifq.cli.plan_recent_taifex_csv_files", lambda *a, **k: plan)
    monkeypatch.setattr(
        "tifq.cli.sync_recent_taifex_csv_files",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("download called")),
    )

    result = runner.invoke(
        app,
        ["sync-taifex", "--raw-dir", str(tmp_path / "raw"), "--plan"],
    )

    assert result.exit_code == 0
    assert "TAIFEX download plan" in result.stdout
    assert not (tmp_path / "raw").exists()


def test_workflow_help_lists_stop_and_machine_readable_options() -> None:
    result = runner.invoke(app, ["workflow", "--help"])

    assert result.exit_code == 0
    assert "--stop-after" in result.stdout
    assert "--quiet" in result.stdout
    assert "--json" in result.stdout


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
