from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import yaml

from tifq.backtest import BacktestResult, persist_backtest_result
from tifq.config.models import BacktestConfig


def config_for_tmp_path(tmp_path: Path) -> BacktestConfig:
    return BacktestConfig.model_validate(
        {
            "project": {
                "name": "Taiwan Index Futures Quant",
                "timezone": "Asia/Taipei",
            },
            "data": {
                "symbol": "TMF",
                "contract_mode": "continuous_front_month",
                "raw_dir": tmp_path / "raw" / "taifex",
                "processed_dir": tmp_path / "processed",
                "start_date": date(2026, 6, 17),
                "end_date": date(2026, 6, 17),
                "session": "day",
                "timeframe": "5m",
            },
            "product": {
                "point_value": 10,
                "tick_size": 1,
                "exchange": "TAIFEX",
            },
            "cost": {
                "commission_per_side": 5,
                "tax_rate": 0.00002,
                "slippage_points_per_side": 1,
            },
            "strategy": {
                "name": "vwap_trend",
                "params": {"ema_fast": 20, "ema_slow": 60},
            },
            "portfolio": {
                "initial_cash": 100_000,
                "max_position": 1,
                "allow_short": True,
            },
        }
    )


def test_persist_backtest_result_writes_required_files(tmp_path: Path) -> None:
    config = config_for_tmp_path(tmp_path)
    result = BacktestResult(
        trades=pd.DataFrame(
            {
                "entry_time": ["2026-06-17 09:05:00+08:00"],
                "exit_time": ["2026-06-17 09:10:00+08:00"],
                "symbol": ["TMF"],
                "side": ["LONG"],
                "qty": [1],
                "entry_price": [101.0],
                "exit_price": [105.0],
                "gross_pnl": [40.0],
                "fee": [10.0],
                "tax": [4.0],
                "slippage": [20.0],
                "net_pnl": [26.0],
                "exit_reason": ["take_profit"],
            }
        ),
        equity_curve=pd.DataFrame(
            {
                "timestamp": ["2026-06-17 09:05:00+08:00"],
                "cash": [100_026.0],
                "position": [0],
                "close": [105.0],
                "equity": [100_026.0],
            }
        ),
        metrics={"initial_cash": 100_000.0, "final_equity": 100_026.0, "trade_count": 1},
    )

    paths = persist_backtest_result(config, result, run_id="unit-test-run")

    assert paths.run_dir == tmp_path / "results" / "backtests" / "vwap_trend" / "unit-test-run"
    assert paths.config_path.exists()
    assert paths.trades_path.exists()
    assert paths.equity_curve_path.exists()
    assert paths.metrics_path.exists()
    assert paths.model_bars_path.exists()
    assert paths.signals_path.exists()
    assert paths.contract_selection_path.exists()
    assert paths.diagnostics_path.exists()
    assert paths.timings_path.exists()
    assert paths.data_fingerprint_path.exists()
    assert yaml.safe_load(paths.config_path.read_text(encoding="utf-8"))["data"]["symbol"] == "TMF"
    assert pd.read_csv(paths.trades_path).loc[0, "exit_reason"] == "take_profit"
    assert pd.read_csv(paths.equity_curve_path).loc[0, "equity"] == 100_026.0
    assert json.loads(paths.metrics_path.read_text(encoding="utf-8"))["trade_count"] == 1


def test_report_failure_removes_staging_and_never_publishes_partial_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = config_for_tmp_path(tmp_path)
    result = BacktestResult(
        trades=pd.DataFrame({"net_pnl": [1.0]}),
        equity_curve=pd.DataFrame({"equity": [100_001.0]}),
        metrics={"trade_count": 1},
    )

    def fail_to_csv(*args: object, **kwargs: object) -> None:
        raise OSError("disk failure")

    monkeypatch.setattr(result.trades, "to_csv", fail_to_csv)

    with pytest.raises(OSError, match="disk failure"):
        persist_backtest_result(config, result, run_id="failed-run")

    strategy_dir = tmp_path / "results" / "backtests" / "vwap_trend"
    assert not (strategy_dir / "failed-run").exists()
    assert not (strategy_dir / ".failed-run.staging").exists()


def test_persist_backtest_result_reports_artifact_progress(tmp_path: Path) -> None:
    config = config_for_tmp_path(tmp_path)
    result = BacktestResult(
        trades=pd.DataFrame(),
        equity_curve=pd.DataFrame(),
        metrics={"trade_count": 0},
    )
    updates = []

    persist_backtest_result(
        config,
        result,
        run_id="progress-run",
        progress_callback=updates.append,
    )

    assert updates[0].phase == "Persist report"
    assert updates[-1].phase == "Complete"
    assert updates[-1].completed == updates[-1].total == 10
