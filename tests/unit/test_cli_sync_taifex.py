from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from tifq.application.dto import PipelineResultDTO
from tifq.cli import app


def test_sync_taifex_cli_orchestrates_download_import_and_bar_build(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[str] = []

    class Pipeline:
        def sync(self, request):
            calls.append(f"sync:{request.raw_dir}:{request.limit}:{request.overwrite}")
            return PipelineResultDTO("sync", 1, 0, (), False, details={"failed": 0})

        def import_ticks(self, request):
            calls.append(f"import:{request.raw_dir}:{request.processed_dir}:{request.symbol}")
            return PipelineResultDTO(
                "import", 1, 0, (), False,
                details={"output_rows": 3, "invalid_rows": 1},
            )

        def build_bars(self, request):
            calls.append(f"build:{request.processed_dir}:{request.symbol}:{request.timeframe}")
            return PipelineResultDTO("bars", 1, 0, (), False, details={"output_bars": 2})

    monkeypatch.setattr("tifq.cli._application", lambda: SimpleNamespace(data_pipeline=Pipeline()))

    result = CliRunner().invoke(
        app,
        [
            "sync-taifex",
            "--raw-dir",
            str(tmp_path / "raw"),
            "--processed-dir",
            str(tmp_path / "processed"),
            "--symbol",
            "TMF",
            "--timeframe",
            "5m",
            "--limit",
            "3",
        ],
    )

    assert result.exit_code == 0
    assert calls == [
        f"sync:{tmp_path / 'raw'}:3:False",
        f"import:{tmp_path / 'raw'}:{tmp_path / 'processed'}:TMF",
        f"build:{tmp_path / 'processed'}:TMF:5m",
    ]
    assert "Built bars: 2" in result.stdout


def test_sync_taifex_cli_download_only_stops_before_import(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []

    class Pipeline:
        def sync(self, request):
            calls.append("sync")
            return PipelineResultDTO("sync", 1, 0, (), False, details={"failed": 0})

    monkeypatch.setattr("tifq.cli._application", lambda: SimpleNamespace(data_pipeline=Pipeline()))

    result = CliRunner().invoke(
        app,
        [
            "sync-taifex",
            "--raw-dir",
            str(tmp_path / "raw"),
            "--download-only",
        ],
    )

    assert result.exit_code == 0
    assert calls == ["sync"]
    assert "Clean TMF ticks" not in result.stdout


def test_sync_taifex_cli_reports_partial_download_failure(tmp_path: Path, monkeypatch) -> None:
    class Pipeline:
        def sync(self, request):
            return PipelineResultDTO(
                "sync", 1, 0, (), False,
                details={"failed": 1, "failure_reasons": ("HTML error page",)},
            )

    monkeypatch.setattr("tifq.cli._application", lambda: SimpleNamespace(data_pipeline=Pipeline()))

    result = CliRunner().invoke(app, ["sync-taifex", "--raw-dir", str(tmp_path / "raw")])

    assert result.exit_code == 1
    assert "Failed: 1" in result.stdout
    assert result.exit_code == 1


def test_sync_taifex_cli_rejects_invalid_symbol_timeframe_and_limit() -> None:
    runner = CliRunner()

    assert runner.invoke(app, ["sync-taifex", "--symbol", "TX"]).exit_code != 0
    assert runner.invoke(app, ["sync-taifex", "--timeframe", "15m"]).exit_code != 0
    assert runner.invoke(app, ["sync-taifex", "--limit", "31"]).exit_code != 0
