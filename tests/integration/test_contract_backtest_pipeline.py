from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from tifq.backtest import (
    BacktestEngine,
    CostModel,
    prepare_backtest,
    run_backtest_from_config,
)
from tifq.config.models import BacktestConfig
from tifq.data.storage import bar_path, write_parquet


def config(tmp_path: Path, **strategy_overrides: object) -> BacktestConfig:
    params = {
        "ema_fast": 2,
        "ema_slow": 3,
        "atr_period": 2,
        "volatility_window": 2,
        "atr_stop_mult": 1.5,
        "take_profit_r": 1.5,
        "min_atr_points": 0,
        "max_atr_points": 120,
        "max_trades_per_day": 3,
        "force_flatten_time": "13:35:00",
        "no_entry_before": "08:45:00",
        "no_entry_after": "13:20:00",
        **strategy_overrides,
    }
    return BacktestConfig.model_validate(
        {
            "project": {"name": "test", "timezone": "Asia/Taipei"},
            "data": {
                "symbol": "TMF",
                "contract_mode": "continuous_front_month",
                "contract": None,
                "roll_confirmation_days": 1,
                "raw_dir": tmp_path / "raw",
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
            "strategy": {"name": "vwap_trend", "params": params},
            "portfolio": {"initial_cash": 100_000, "max_position": 1, "allow_short": True},
        }
    )


def multi_contract_bars() -> pd.DataFrame:
    rows = []
    for index in range(12):
        timestamp = pd.Timestamp("2026-06-17 08:45", tz="Asia/Taipei") + pd.Timedelta(
            minutes=5 * index
        )
        for contract, offset, volume in (("202606", 0.0, 100), ("202607", 500.0, 10)):
            close = 100.0 + index + offset
            rows.append(
                {
                    "symbol": "TMF",
                    "contract": contract,
                    "timeframe": "5m",
                    "timestamp": timestamp,
                    "open": close,
                    "high": close + 2,
                    "low": close - 2,
                    "close": close,
                    "volume": volume,
                }
            )
    return pd.DataFrame(rows)


def test_config_backtest_selects_one_active_contract_and_aligns_artifacts(
    tmp_path: Path,
) -> None:
    selected_config = config(tmp_path)
    source = bar_path(selected_config.data.processed_dir, "TMF", "5m", date(2026, 6, 17))
    write_parquet(multi_contract_bars(), source)

    result = run_backtest_from_config(selected_config)

    assert len(result.model_bars) == 12
    assert result.model_bars["timestamp"].is_unique
    assert set(result.model_bars["contract"]) == {"202606"}
    assert result.model_bars["contract_segment_id"].nunique() == 1
    assert result.signals["timestamp"].tolist() == result.model_bars["timestamp"].tolist()
    assert result.signals["contract"].tolist() == result.model_bars["contract"].tolist()
    assert result.contract_selection.loc[0, "selected_contract"] == "202606"
    assert result.diagnostics["stats"]["active_contracts_per_day_max"] == 1
    assert result.data_fingerprint["source_bar_paths"] == [str(source.resolve())]


def test_zero_trade_diagnostics_identifies_atr_above_maximum(tmp_path: Path) -> None:
    selected_config = config(tmp_path, max_atr_points=0.1)
    write_parquet(
        multi_contract_bars(),
        bar_path(selected_config.data.processed_dir, "TMF", "5m", date(2026, 6, 17)),
    )

    result = run_backtest_from_config(selected_config)

    assert result.metrics["trade_count"] == 0
    assert result.diagnostics["primary_zero_trade_reason"] == "ATR above maximum"
    assert result.diagnostics["stats"]["atr_in_range_ratio"] == 0


def test_assumed_margin_blocks_entry_and_records_reason() -> None:
    bars = (
        multi_contract_bars()
        .loc[lambda frame: frame["contract"] == "202606"]
        .head(3)
        .reset_index(drop=True)
    )
    signals = pd.DataFrame(
        {
            "timestamp": bars["timestamp"],
            "symbol": "TMF",
            "side": ["BUY", "HOLD", "HOLD"],
            "target_position": [1, 1, 1],
            "reason": ["long_entry", "hold", "hold"],
            "stop_loss": [None, None, None],
            "take_profit": [None, None, None],
        }
    )
    signals["contract"] = bars["contract"].to_numpy()
    signals["contract_segment_id"] = "segment_001"
    bars["contract_segment_id"] = "segment_001"
    engine = BacktestEngine(
        initial_cash=1_000,
        cost_model=CostModel(point_value=10),
        assumed_margin_per_contract=50_000,
    )

    result = engine.run(bars, signals)

    assert result.metrics["trade_count"] == 0
    assert result.diagnostics["execution_rejections"] == {"insufficient_assumed_margin": 2}


def test_preflight_fingerprint_change_is_rejected(tmp_path: Path) -> None:
    selected_config = config(tmp_path)
    source = bar_path(selected_config.data.processed_dir, "TMF", "5m", date(2026, 6, 17))
    bars = multi_contract_bars()
    write_parquet(bars, source)
    prepared = prepare_backtest(selected_config)
    bars.loc[0, "close"] += 1
    write_parquet(bars, source)

    with pytest.raises(ValueError, match="stale"):
        run_backtest_from_config(selected_config, preflight=prepared)


def test_matching_preflight_is_reused(tmp_path: Path) -> None:
    selected_config = config(tmp_path)
    write_parquet(
        multi_contract_bars(),
        bar_path(selected_config.data.processed_dir, "TMF", "5m", date(2026, 6, 17)),
    )
    prepared = prepare_backtest(selected_config)

    result = run_backtest_from_config(selected_config, preflight=prepared)

    assert result.timings["preflight_reused"] == 1.0
