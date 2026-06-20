"""Persisted backtest result discovery and loading service."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pandas as pd
import yaml

from tifq.application.dto import ComparisonDTO, LoadedRunDTO, ResultSummaryDTO
from tifq.config.models import BacktestConfig
from tifq.workflow import REQUIRED_RESULT_ARTIFACTS, discover_latest_matching_result


class ResultService:
    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()
        self.results_root = self.repository_root / "data" / "results" / "backtests"

    def list_runs(self, query: str | None = None) -> tuple[ResultSummaryDTO, ...]:
        if not self.results_root.exists():
            return ()
        runs: list[ResultSummaryDTO] = []
        for config_path in self.results_root.glob("*/*/config.yaml"):
            run_dir = config_path.parent
            if query and query not in str(run_dir):
                continue
            metrics = self._json(run_dir / "metrics.json")
            status = {name: (run_dir / name).exists() for name in REQUIRED_RESULT_ARTIFACTS}
            runs.append(
                ResultSummaryDTO(
                    run_dir.name,
                    run_dir.parent.name,
                    metrics,
                    not all(status.values()),
                    status,
                    str(run_dir),
                )
            )
        return tuple(sorted(runs, key=lambda run: Path(run.run_dir).stat().st_mtime, reverse=True))

    def get_run(self, run_id: str) -> LoadedRunDTO:
        matches = [run for run in self.list_runs() if run.run_id == run_id]
        if len(matches) != 1:
            raise FileNotFoundError(f"Expected one result run named {run_id}; found {len(matches)}")
        summary = matches[0]
        root = Path(summary.run_dir)
        config = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8")) or {}
        return LoadedRunDTO(
            summary,
            config,
            summary.metrics,
            self._records(root / "trades.csv"),
            self._records(root / "equity_curve.csv"),
            str(root / "model_bars.parquet") if (root / "model_bars.parquet").exists() else None,
            self._records(root / "signals.csv"),
            self._records(root / "contract_selection.csv"),
            self._json(root / "diagnostics.json"),
            {key: float(value) for key, value in self._json(root / "timings.json").items()},
        )

    def compare_runs(self, run_ids: tuple[str, ...]) -> ComparisonDTO:
        records = tuple(
            {
                "run_id": loaded.summary.run_id,
                "strategy": loaded.summary.strategy,
                **loaded.metrics,
            }
            for loaded in (self.get_run(run_id) for run_id in run_ids)
        )
        return ComparisonDTO(records)

    def find_latest_matching(self, config: BacktestConfig) -> ResultSummaryDTO | None:
        path = discover_latest_matching_result(config)
        if path is None:
            return None
        return next((run for run in self.list_runs() if Path(run.run_dir) == path), None)

    @staticmethod
    def _json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _records(path: Path) -> tuple[dict[str, Any], ...]:
        if not path.exists():
            return ()
        return tuple(cast(list[dict[str, Any]], pd.read_csv(path).to_dict(orient="records")))
