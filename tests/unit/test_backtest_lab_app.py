from __future__ import annotations

import json
from datetime import date, time
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import pytest
import yaml

from tifq.apps.backtest_lab import (
    ResultRun,
    _element_key,
    _load_chart_bars,
    _render_charts,
    _sync_display_payload,
    build_config_override,
    build_run_comparison_table,
    discover_raw_files,
    discover_result_runs,
    load_result_run,
)
from tifq.config.models import BacktestConfig
from tifq.data.taifex_fetcher import TaifexDownloadFailure, TaifexFetchSummary


class ChartRecorder:
    def __init__(self) -> None:
        self.plotly_keys: list[str] = []
        self.line_keys: list[str] = []
        self.bar_keys: list[str] = []

    def plotly_chart(self, figure: object, *, key: str, **kwargs: object) -> None:
        self.plotly_keys.append(key)

    def line_chart(self, data: object, *, key: str) -> None:
        self.line_keys.append(key)

    def bar_chart(self, data: object, *, key: str) -> None:
        self.bar_keys.append(key)

    def warning(self, message: str) -> None:
        pass

    def write(self, message: str) -> None:
        pass


class LegacyNativeChartRecorder:
    def __init__(self) -> None:
        self.vega_keys: list[str] = []

    def line_chart(self, data: object) -> None:
        raise AssertionError("line_chart without a key must not be used")

    def bar_chart(self, data: object) -> None:
        raise AssertionError("bar_chart without a key must not be used")

    def vega_lite_chart(
        self,
        data: object,
        spec: object,
        *,
        key: str,
        **kwargs: object,
    ) -> None:
        self.vega_keys.append(key)

    def warning(self, message: str) -> None:
        pass

    def write(self, message: str) -> None:
        pass


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


def test_load_chart_bars_calculates_indicators_before_tail(
    tmp_path: Path,
    monkeypatch,
) -> None:
    bars = pd.DataFrame(
        {
            "timestamp": pd.date_range(
                "2026-06-17 08:45:00",
                periods=600,
                freq="min",
                tz="Asia/Taipei",
            ),
            "symbol": ["TMF"] * 600,
            "contract": ["202606"] * 600,
            "timeframe": ["1m"] * 600,
            "open": list(range(600)),
            "high": list(range(1, 601)),
            "low": list(range(600)),
            "close": list(range(600)),
            "volume": [1] * 600,
        }
    )
    observed_lengths: list[int] = []

    def fake_load_configured_bars(config: BacktestConfig) -> pd.DataFrame:
        return bars

    def fake_append_basic_indicators(input_bars: pd.DataFrame, **kwargs: object) -> pd.DataFrame:
        observed_lengths.append(len(input_bars))
        result = input_bars.copy()
        result["ema_fast"] = range(len(result))
        result["ema_slow"] = range(len(result))
        result["vwap"] = range(len(result))
        result["atr"] = range(len(result))
        result["realized_volatility"] = range(len(result))
        return result

    monkeypatch.setattr("tifq.apps.backtest_lab.load_configured_bars", fake_load_configured_bars)
    monkeypatch.setattr(
        "tifq.apps.backtest_lab.append_basic_indicators",
        fake_append_basic_indicators,
    )

    chart_bars = _load_chart_bars(base_config(tmp_path))

    assert observed_lengths == [600]
    assert len(chart_bars) == 500
    assert chart_bars.loc[0, "ema_fast"] == 100


def test_sync_display_payload_keeps_import_and_build_empty_when_downloads_fail(
    tmp_path: Path,
) -> None:
    summary = TaifexFetchSummary(
        files_discovered=2,
        files_selected=2,
        files_downloaded=1,
        files_skipped=0,
        files_updated=0,
        files_failed=1,
        records=(),
        failures=(
            TaifexDownloadFailure(
                trading_date=date(2026, 6, 17),
                download_url="https://www.taifex.com.tw/file/Daily_20260617.csv",
                remote_filename="Daily_20260617.csv",
                local_path=tmp_path / "Daily_20260617.csv",
                error="HTML error page",
            ),
        ),
    )

    payload = _sync_display_payload(summary, None, None)

    assert payload["failed"] == 1
    assert payload["failures"][0]["error"] == "HTML error page"
    assert "imported_tick_count" not in payload
    assert "built_bar_count" not in payload


def test_render_charts_requires_key_prefix() -> None:
    recorder = ChartRecorder()

    with pytest.raises(TypeError, match="key_prefix"):
        _render_charts(recorder, go, pd.DataFrame(), pd.DataFrame(), pd.DataFrame())


def test_plotly_chart_keys_are_unique_across_backtest_and_result_browser() -> None:
    equity_curve, trades, bars = chart_frames()
    backtest = ChartRecorder()
    result_browser = ChartRecorder()

    _render_charts(
        backtest,
        go,
        equity_curve,
        trades,
        bars,
        key_prefix="run_backtest",
    )
    _render_charts(
        result_browser,
        go,
        equity_curve,
        trades,
        bars,
        key_prefix="result_browser_run-001",
    )

    assert backtest.plotly_keys == [
        "run_backtest_equity_curve",
        "run_backtest_daily_pnl",
        "run_backtest_kline",
    ]
    assert result_browser.plotly_keys == [
        "result_browser_run-001_equity_curve",
        "result_browser_run-001_daily_pnl",
        "result_browser_run-001_kline",
    ]
    assert set(backtest.plotly_keys).isdisjoint(result_browser.plotly_keys)


def test_result_browser_run_ids_produce_different_stable_chart_keys() -> None:
    equity_curve, trades, bars = chart_frames()
    first = ChartRecorder()
    repeated = ChartRecorder()
    second = ChartRecorder()

    for recorder, run_id in ((first, "run:001"), (repeated, "run:001"), (second, "run/002")):
        _render_charts(
            recorder,
            go,
            equity_curve,
            trades,
            bars,
            key_prefix=f"result_browser_{run_id}",
        )

    assert first.plotly_keys == repeated.plotly_keys
    assert set(first.plotly_keys).isdisjoint(second.plotly_keys)


def test_native_fallback_chart_keys_are_unique() -> None:
    equity_curve, trades, bars = chart_frames()
    recorder = ChartRecorder()

    _render_charts(
        recorder,
        None,
        equity_curve,
        trades,
        bars,
        key_prefix="native/run 001",
    )

    assert recorder.line_keys == [
        "native_run_001_native_equity",
        "native_run_001_native_indicators",
    ]
    assert recorder.bar_keys == ["native_run_001_native_daily_pnl"]


def test_native_fallback_uses_keyed_vega_when_streamlit_chart_api_has_no_key() -> None:
    equity_curve, trades, bars = chart_frames()
    recorder = LegacyNativeChartRecorder()

    _render_charts(
        recorder,
        None,
        equity_curve,
        trades,
        bars,
        key_prefix="legacy_native",
    )

    assert recorder.vega_keys == [
        "legacy_native_native_equity",
        "legacy_native_native_daily_pnl",
        "legacy_native_native_indicators",
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("run 001", "run_001"),
        ("strategy/run:001", "strategy_run_001"),
        ("run#001", "run_001"),
        ("2026-06-19T08:45:00+08:00", "2026-06-19T08_45_00_08_00"),
        ("", "element"),
    ],
)
def test_element_key_is_safe_and_deterministic(value: str, expected: str) -> None:
    assert _element_key(value) == expected
    assert _element_key(value) == expected


def chart_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    timestamp = pd.Timestamp("2026-06-17 09:00:00", tz="Asia/Taipei")
    equity_curve = pd.DataFrame({"timestamp": [timestamp], "equity": [100_100.0]})
    trades = pd.DataFrame(
        {
            "entry_time": [timestamp],
            "exit_time": [timestamp + pd.Timedelta(minutes=5)],
            "entry_price": [22_000.0],
            "exit_price": [22_010.0],
            "net_pnl": [100.0],
        }
    )
    bars = pd.DataFrame(
        {
            "timestamp": [timestamp],
            "open": [22_000.0],
            "high": [22_012.0],
            "low": [21_998.0],
            "close": [22_010.0],
            "vwap": [22_005.0],
            "ema_fast": [22_006.0],
            "ema_slow": [22_004.0],
        }
    )
    return equity_curve, trades, bars


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
