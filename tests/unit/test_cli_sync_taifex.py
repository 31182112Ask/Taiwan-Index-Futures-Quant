from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from typer.testing import CliRunner

from tifq.cli import app
from tifq.data.taifex_fetcher import (
    TaifexDownloadRecord,
    TaifexFetchSummary,
)


@dataclass(frozen=True)
class DummyImportSummary:
    output_tick_count: int = 3
    invalid_row_count: int = 1


@dataclass(frozen=True)
class DummyBarSummary:
    output_bar_count: int = 2


def fetch_summary(tmp_path: Path) -> TaifexFetchSummary:
    return TaifexFetchSummary(
        files_discovered=1,
        files_selected=1,
        files_downloaded=1,
        files_skipped=0,
        files_updated=0,
        records=(
            TaifexDownloadRecord(
                trading_date=date(2026, 6, 18),
                source_url="https://www.taifex.com.tw/cht/3/dlFutPrevious30DaysSalesData",
                local_path=tmp_path / "official" / "2026-06-18" / "Daily_20260618.csv",
                size_bytes=10,
                sha256="abc",
                status="downloaded",
            ),
        ),
    )


def test_sync_taifex_cli_orchestrates_download_import_and_bar_build(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[str] = []

    def fake_sync(raw_dir: Path, **kwargs: object) -> TaifexFetchSummary:
        calls.append(f"sync:{raw_dir}:{kwargs['limit']}:{kwargs['overwrite']}")
        return fetch_summary(tmp_path)

    def fake_import(raw_dir: Path, processed_dir: Path, *, symbol: str) -> DummyImportSummary:
        calls.append(f"import:{raw_dir}:{processed_dir}:{symbol}")
        return DummyImportSummary()

    def fake_build(processed_dir: Path, *, symbol: str, timeframe: str) -> DummyBarSummary:
        calls.append(f"build:{processed_dir}:{symbol}:{timeframe}")
        return DummyBarSummary()

    monkeypatch.setattr("tifq.cli.sync_recent_taifex_csv_files", fake_sync)
    monkeypatch.setattr("tifq.cli.import_taifex_ticks", fake_import)
    monkeypatch.setattr("tifq.cli.build_bar_files", fake_build)

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

    def fake_sync(raw_dir: Path, **kwargs: object) -> TaifexFetchSummary:
        calls.append("sync")
        return fetch_summary(tmp_path)

    monkeypatch.setattr("tifq.cli.sync_recent_taifex_csv_files", fake_sync)

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


def test_sync_taifex_cli_rejects_invalid_symbol_timeframe_and_limit() -> None:
    runner = CliRunner()

    assert runner.invoke(app, ["sync-taifex", "--symbol", "TX"]).exit_code != 0
    assert runner.invoke(app, ["sync-taifex", "--timeframe", "15m"]).exit_code != 0
    assert runner.invoke(app, ["sync-taifex", "--limit", "31"]).exit_code != 0
