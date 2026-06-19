"""Persist V1 backtest results to reproducible output files."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from tifq.backtest.engine import BacktestResult
from tifq.config.models import BacktestConfig
from tifq.data.storage import write_parquet
from tifq.runtime.locking import PipelineOperationLock
from tifq.runtime.progress import ProgressCallback, ProgressReporter


@dataclass(frozen=True)
class BacktestReportPaths:
    """Paths written for one persisted backtest run."""

    run_dir: Path
    config_path: Path
    trades_path: Path
    equity_curve_path: Path
    metrics_path: Path
    model_bars_path: Path
    signals_path: Path
    contract_selection_path: Path
    diagnostics_path: Path
    timings_path: Path
    data_fingerprint_path: Path


def persist_backtest_result(
    config: BacktestConfig,
    result: BacktestResult,
    *,
    results_dir: str | Path | None = None,
    run_id: str | None = None,
    progress_callback: ProgressCallback | None = None,
) -> BacktestReportPaths:
    """Stage every reproducibility artifact, then atomically publish the run."""
    progress = ProgressReporter("persist_backtest_report", progress_callback)
    base_dir = Path(results_dir) if results_dir is not None else _default_results_dir(config)
    selected_run_id = run_id or make_run_id()
    run_dir = base_dir / config.strategy.name / selected_run_id
    staging_dir = run_dir.with_name(f".{selected_run_id}.staging")
    if run_dir.exists() or staging_dir.exists():
        raise FileExistsError(f"Backtest run path already exists: {run_dir}")
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=False, exist_ok=False)

    config_path = staging_dir / "config.yaml"
    trades_path = staging_dir / "trades.csv"
    equity_curve_path = staging_dir / "equity_curve.csv"
    metrics_path = staging_dir / "metrics.json"
    model_bars_path = staging_dir / "model_bars.parquet"
    signals_path = staging_dir / "signals.csv"
    contract_selection_path = staging_dir / "contract_selection.csv"
    diagnostics_path = staging_dir / "diagnostics.json"
    timings_path = staging_dir / "timings.json"
    data_fingerprint_path = staging_dir / "data_fingerprint.json"
    started = perf_counter()
    try:
        with PipelineOperationLock(
            config.data.processed_dir.parent / ".runtime", "report_persistence"
        ):
            progress.update("Persist report", 0, 10, "Writing reproducibility artifacts")
            config_payload = config.model_dump(mode="json")
            config_path.write_text(
                yaml.safe_dump(config_payload, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            progress.update("Persist report", 1, 10, "Saved config.yaml")
            result.trades.to_csv(trades_path, index=False)
            progress.update("Persist report", 2, 10, "Saved trades.csv")
            result.equity_curve.to_csv(equity_curve_path, index=False)
            progress.update("Persist report", 3, 10, "Saved equity_curve.csv")
            metrics_path.write_text(
                json.dumps(result.metrics, indent=2, sort_keys=True, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            progress.update("Persist report", 4, 10, "Saved metrics.json")
            model_bars = result.model_bars
            if model_bars.empty and not len(model_bars.columns):
                model_bars = _empty_model_bars()
            write_parquet(model_bars, model_bars_path)
            progress.update("Persist report", 5, 10, "Saved model_bars.parquet")
            signals = result.signals
            if signals.empty and not len(signals.columns):
                signals = _empty_signals()
            signals.to_csv(signals_path, index=False)
            progress.update("Persist report", 6, 10, "Saved signals.csv")
            contract_selection = result.contract_selection
            if contract_selection.empty and not len(contract_selection.columns):
                contract_selection = _empty_contract_selection()
            contract_selection.to_csv(contract_selection_path, index=False)
            progress.update("Persist report", 7, 10, "Saved contract_selection.csv")
            diagnostics_path.write_text(
                json.dumps(result.diagnostics, indent=2, sort_keys=True, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            progress.update("Persist report", 8, 10, "Saved diagnostics.json")
            timings = dict(result.timings)
            timings["report_persistence"] = perf_counter() - started
            timings_path.write_text(
                json.dumps(timings, indent=2, sort_keys=True, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            progress.update("Persist report", 9, 10, "Saved timings.json")
            data_fingerprint_path.write_text(
                json.dumps(
                    result.data_fingerprint,
                    indent=2,
                    sort_keys=True,
                    allow_nan=False,
                )
                + "\n",
                encoding="utf-8",
            )
            progress.update("Persist report", 10, 10, "Saved data_fingerprint.json")
            staging_dir.rename(run_dir)
            progress.update("Complete", 10, 10, f"Published {run_dir.name}")
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    def published(path: Path) -> Path:
        return run_dir / path.name

    return BacktestReportPaths(
        run_dir=run_dir,
        config_path=published(config_path),
        trades_path=published(trades_path),
        equity_curve_path=published(equity_curve_path),
        metrics_path=published(metrics_path),
        model_bars_path=published(model_bars_path),
        signals_path=published(signals_path),
        contract_selection_path=published(contract_selection_path),
        diagnostics_path=published(diagnostics_path),
        timings_path=published(timings_path),
        data_fingerprint_path=published(data_fingerprint_path),
    )


def make_run_id(now: datetime | None = None) -> str:
    """Return a filesystem-friendly Asia/Taipei timestamp run id."""
    timestamp = now or datetime.now(tz=ZoneInfo("Asia/Taipei"))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=ZoneInfo("Asia/Taipei"))
    return timestamp.strftime("%Y%m%dT%H%M%S%f%z")


def _default_results_dir(config: BacktestConfig) -> Path:
    return config.data.processed_dir.parent / "results" / "backtests"


def _empty_model_bars() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "symbol",
            "contract",
            "contract_segment_id",
            "timeframe",
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    )


def _empty_signals() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "timestamp",
            "symbol",
            "side",
            "target_position",
            "reason",
            "stop_loss",
            "take_profit",
        ]
    )


def _empty_contract_selection() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "trading_date",
            "selected_contract",
            "selection_reason",
            "current_volume",
            "next_contract",
            "next_volume",
            "rolled",
            "contract_segment_id",
            "decision_source_date",
            "decision_current_volume",
            "decision_next_volume",
            "confirmation_count",
            "roll_effective_date",
        ]
    )
