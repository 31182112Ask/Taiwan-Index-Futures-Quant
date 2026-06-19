from __future__ import annotations

import json
from datetime import date, time
from pathlib import Path

import pandas as pd
import yaml

from tifq.apps.backtest_lab import (
    ResultRun,
    build_config_override,
    build_run_comparison_table,
    discover_raw_files,
    discover_result_runs,
    load_result_run,
)
from tifq.config.models import BacktestConfig


def base_config(tmp_path: Path) -> BacktestConfig:
    return BacktestConfig.model_validate(
        {
            "project": {"name": "Taiwan Index Futures Quant", "timezone": "Asia/Taipei"},
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
            "product": {"point_value": 10, "tick_size": 1, "exchange": "TAIFEX"},
            "cost": {
                "commission_per_side": 5,
                "tax_rate": 0.00002,
                "slippage_points_per_side": 1,
            },
            "strategy": {
                "name": "vwap_trend",
                "params": {
                    "ema_fast": 20,
                    "ema_slow": 60,
                    "atr_period": 14,
                },
            },
            "portfolio": {"initial_cash": 100_000, "max_position": 1, "allow_short": True},
        }
    )


def test_build_config_override_returns_valid_v1_config(tmp_path: Path) -> None:
    config = build_config_override(
        base_config(tmp_path),
        raw_dir=tmp_path / "raw" / "taifex",
        processed_dir=tmp_path / "processed",
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 17),
        timeframe="1m",
        ema_fast=10,
        ema_slow=30,
        atr_period=7,
        atr_stop_mult=2.0,
        take_profit_r=1.2,
        min_atr_points=5,
        max_atr_points=90,
        max_trades_per_day=2,
        force_flatten_time=time(13, 35),
        no_entry_before=time(8, 55),
        no_entry_after=time(13, 20),
        commission_per_side=6,
        tax_rate=0.00003,
        slippage_points_per_side=2,
        initial_cash=120_000,
        max_position=1,
        allow_short=False,
    )

    assert config.data.timeframe == "1m"
    assert config.strategy.params["ema_fast"] == 10
    assert config.strategy.params["force_flatten_time"] == "13:35:00"
    assert config.cost.commission_per_side == 6
    assert config.portfolio.allow_short is False


def test_discover_raw_files_returns_csv_and_zip_only(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    csv_path = raw_dir / "ticks.csv"
    zip_path = raw_dir / "ticks.zip"
    txt_path = raw_dir / "notes.txt"
    csv_path.write_text("symbol\nTMF\n", encoding="utf-8")
    zip_path.write_bytes(b"placeholder")
    txt_path.write_text("ignored", encoding="utf-8")

    assert discover_raw_files(raw_dir) == [csv_path, zip_path]


def test_discover_and_load_result_runs(tmp_path: Path) -> None:
    run_dir = tmp_path / "results" / "backtests" / "vwap_trend" / "run-001"
    write_result_run(run_dir)

    runs = discover_result_runs(tmp_path / "results" / "backtests")
    loaded = load_result_run(runs[0].run_dir)

    assert runs[0].strategy == "vwap_trend"
    assert runs[0].run_id == "run-001"
    assert loaded.config["data"]["timeframe"] == "5m"
    assert loaded.metrics["trade_count"] == 1
    assert loaded.trades.loc[0, "exit_reason"] == "take_profit"
    assert loaded.equity_curve.loc[0, "equity"] == 100_500.0


def test_build_run_comparison_table_includes_required_columns(tmp_path: Path) -> None:
    run_one = tmp_path / "results" / "backtests" / "vwap_trend" / "run-001"
    run_two = tmp_path / "results" / "backtests" / "vwap_trend" / "run-002"
    write_result_run(run_one, ema_fast=20, net_pnl=500.0)
    write_result_run(run_two, ema_fast=30, net_pnl=-100.0)

    comparison = build_run_comparison_table(
        [
            (
                ResultRun("vwap_trend", "run-001", run_one, 1.0),
                load_result_run(run_one),
            ),
            (
                ResultRun("vwap_trend", "run-002", run_two, 2.0),
                load_result_run(run_two),
            ),
        ]
    )

    assert comparison.columns.tolist() == [
        "run_id",
        "date_range",
        "timeframe",
        "ema_fast",
        "ema_slow",
        "atr_period",
        "atr_stop_mult",
        "take_profit_r",
        "commission",
        "slippage",
        "net_pnl",
        "max_drawdown",
        "win_rate",
        "profit_factor",
        "trade_count",
    ]
    assert comparison["run_id"].tolist() == ["run-001", "run-002"]
    assert comparison["date_range"].tolist() == [
        "2026-06-01 to 2026-06-17",
        "2026-06-01 to 2026-06-17",
    ]
    assert comparison["ema_fast"].tolist() == [20, 30]
    assert comparison["net_pnl"].tolist() == [500.0, -100.0]


def write_result_run(run_dir: Path, *, ema_fast: int = 20, net_pnl: float = 500.0) -> None:
    run_dir.mkdir(parents=True)
    config = {
        "data": {
            "start_date": "2026-06-01",
            "end_date": "2026-06-17",
            "timeframe": "5m",
        },
        "strategy": {
            "params": {
                "ema_fast": ema_fast,
                "ema_slow": 60,
                "atr_period": 14,
                "atr_stop_mult": 1.5,
                "take_profit_r": 1.5,
            }
        },
        "cost": {
            "commission_per_side": 5,
            "slippage_points_per_side": 1,
        },
        "portfolio": {
            "initial_cash": 100_000,
            "max_position": 1,
            "allow_short": True,
        },
    }
    (run_dir / "config.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False),
        encoding="utf-8",
    )
    (run_dir / "metrics.json").write_text(
        json.dumps(
            {
                "final_equity": 100_000.0 + net_pnl,
                "net_pnl": net_pnl,
                "max_drawdown": 200.0,
                "win_rate": 0.5,
                "profit_factor": 1.2,
                "trade_count": 1,
            }
        ),
        encoding="utf-8",
    )
    pd.DataFrame({"exit_reason": ["take_profit"], "net_pnl": [net_pnl]}).to_csv(
        run_dir / "trades.csv",
        index=False,
    )
    pd.DataFrame(
        {"timestamp": ["2026-06-17 09:05:00+08:00"], "equity": [100_000.0 + net_pnl]}
    ).to_csv(
        run_dir / "equity_curve.csv",
        index=False,
    )
