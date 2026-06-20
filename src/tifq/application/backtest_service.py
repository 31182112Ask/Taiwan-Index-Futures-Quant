"""Backtest preparation, execution, and persistence application service."""

from __future__ import annotations

from tifq.application._progress import progress_callback
from tifq.application.dto import BacktestRunDTO, PreflightDTO, PreparedBacktest
from tifq.application.ports import ProgressSink
from tifq.backtest import persist_backtest_result, prepare_backtest, run_backtest_from_config
from tifq.backtest.engine import BacktestPreflight
from tifq.config.models import BacktestConfig


class BacktestService:
    def preflight(
        self, config: BacktestConfig, progress_sink: ProgressSink | None = None
    ) -> PreparedBacktest:
        core = prepare_backtest(config, progress_callback=progress_callback(progress_sink))
        bars = core.model_bars
        contracts = tuple(sorted(bars["contract"].astype(str).unique()))
        trading_days = int(bars["timestamp"].dt.date.nunique()) if len(bars) else 0
        return PreparedBacktest(
            PreflightDTO(
                core.data_fingerprint,
                core.diagnostics,
                len(core.model_bars),
                len(core.signals),
                trading_days,
                contracts,
            ),
            core,
        )

    def run(
        self,
        config: BacktestConfig,
        prepared: PreparedBacktest | None = None,
        progress_sink: ProgressSink | None = None,
    ) -> BacktestRunDTO:
        core_preflight = None
        if prepared is not None:
            if not isinstance(prepared._core, BacktestPreflight):
                raise TypeError("PreparedBacktest does not contain a valid V1 preflight")
            core_preflight = prepared._core
        callback = progress_callback(progress_sink)
        result = run_backtest_from_config(
            config, preflight=core_preflight, progress_callback=callback
        )
        paths = persist_backtest_result(config, result, progress_callback=callback)
        return BacktestRunDTO(
            paths.run_dir.name,
            str(paths.run_dir),
            dict(result.metrics),
            result.diagnostics,
            result.timings,
        )
