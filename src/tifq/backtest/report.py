"""Persist V1 backtest results to reproducible output files."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from tifq.backtest.engine import BacktestResult
from tifq.config.models import BacktestConfig


@dataclass(frozen=True)
class BacktestReportPaths:
    """Paths written for one persisted backtest run."""

    run_dir: Path
    config_path: Path
    trades_path: Path
    equity_curve_path: Path
    metrics_path: Path


def persist_backtest_result(
    config: BacktestConfig,
    result: BacktestResult,
    *,
    results_dir: str | Path | None = None,
    run_id: str | None = None,
) -> BacktestReportPaths:
    """Save config, trades, equity curve, and metrics under one run directory."""
    base_dir = Path(results_dir) if results_dir is not None else _default_results_dir(config)
    selected_run_id = run_id or make_run_id()
    run_dir = base_dir / config.strategy.name / selected_run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    config_path = run_dir / "config.yaml"
    trades_path = run_dir / "trades.csv"
    equity_curve_path = run_dir / "equity_curve.csv"
    metrics_path = run_dir / "metrics.json"

    config_payload = config.model_dump(mode="json")
    config_path.write_text(
        yaml.safe_dump(config_payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    result.trades.to_csv(trades_path, index=False)
    result.equity_curve.to_csv(equity_curve_path, index=False)
    metrics_path.write_text(
        json.dumps(result.metrics, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    return BacktestReportPaths(
        run_dir=run_dir,
        config_path=config_path,
        trades_path=trades_path,
        equity_curve_path=equity_curve_path,
        metrics_path=metrics_path,
    )


def make_run_id(now: datetime | None = None) -> str:
    """Return a filesystem-friendly Asia/Taipei timestamp run id."""
    timestamp = now or datetime.now(tz=ZoneInfo("Asia/Taipei"))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=ZoneInfo("Asia/Taipei"))
    return timestamp.strftime("%Y%m%dT%H%M%S%f%z")


def _default_results_dir(config: BacktestConfig) -> Path:
    return config.data.processed_dir.parent / "results" / "backtests"
