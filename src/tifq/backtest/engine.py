"""Conservative next-bar-open historical backtest engine."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import Any

import pandas as pd

from tifq.backtest.contracts import select_contract_bars
from tifq.backtest.cost import CostModel
from tifq.backtest.diagnostics import build_backtest_diagnostics
from tifq.backtest.metrics import calculate_metrics
from tifq.backtest.portfolio import Portfolio, PositionSide
from tifq.config.models import BacktestConfig
from tifq.data.schemas import V1_SYMBOL, validate_bar_frame
from tifq.data.storage import read_parquet
from tifq.indicators import append_basic_indicators
from tifq.runtime.manifests import sha256_file
from tifq.runtime.progress import ProgressCallback, ProgressReporter
from tifq.strategy.signals import validate_signal_frame
from tifq.strategy.vwap_trend import VWAPTrendStrategy


@dataclass(frozen=True)
class BacktestResult:
    """In-memory result of a V1 backtest execution."""

    trades: pd.DataFrame
    equity_curve: pd.DataFrame
    metrics: dict[str, float | int]
    model_bars: pd.DataFrame = field(default_factory=pd.DataFrame)
    signals: pd.DataFrame = field(default_factory=pd.DataFrame)
    contract_selection: pd.DataFrame = field(default_factory=pd.DataFrame)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    data_fingerprint: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BacktestPreflight:
    """Prepared immutable-by-convention model inputs bound to a data fingerprint."""

    model_bars: pd.DataFrame
    signals: pd.DataFrame
    contract_selection: pd.DataFrame
    diagnostics: dict[str, Any]
    timings: dict[str, float]
    data_fingerprint: dict[str, Any]


class BacktestEngine:
    """Execute strategy signals using next-bar-open fills."""

    def __init__(
        self,
        *,
        initial_cash: float,
        cost_model: CostModel,
        max_position: int = 1,
        allow_short: bool = True,
        max_trades_per_day: int | None = None,
        assumed_margin_per_contract: float | None = None,
    ) -> None:
        if max_position < 0:
            raise ValueError("max_position must be non-negative")
        if max_trades_per_day is not None and max_trades_per_day < 0:
            raise ValueError("max_trades_per_day must be non-negative")
        self.initial_cash = initial_cash
        self.cost_model = cost_model
        self.max_position = max_position
        self.allow_short = allow_short
        self.max_trades_per_day = max_trades_per_day
        self.assumed_margin_per_contract = assumed_margin_per_contract
        self.rejection_counts: dict[str, int] = {}

    @classmethod
    def from_config(cls, config: BacktestConfig) -> BacktestEngine:
        """Create an engine from validated YAML config."""
        return cls(
            initial_cash=config.portfolio.initial_cash,
            cost_model=CostModel(
                point_value=config.product.point_value,
                commission_per_side=config.cost.commission_per_side,
                tax_rate=config.cost.tax_rate,
                slippage_points_per_side=config.cost.slippage_points_per_side,
            ),
            max_position=config.portfolio.max_position,
            allow_short=config.portfolio.allow_short,
            max_trades_per_day=_optional_int(config.strategy.params.get("max_trades_per_day")),
            assumed_margin_per_contract=config.portfolio.assumed_margin_per_contract,
        )

    def run(self, bars: pd.DataFrame, signals: pd.DataFrame) -> BacktestResult:
        """Run a backtest where signal i executes at bar i+1 open."""
        working_bars = _prepare_bars(bars)
        working_signals = _prepare_signals(signals)
        if len(working_bars) != len(working_signals):
            raise ValueError("bars and signals must have the same number of rows")
        _validate_bar_signal_alignment(working_bars, working_signals)
        self.rejection_counts = {}

        portfolio = Portfolio(self.initial_cash)
        trade_counts: dict[date, int] = {}
        equity_rows: list[dict[str, Any]] = []

        for index, (_, bar) in enumerate(working_bars.iterrows()):
            timestamp = pd.Timestamp(bar["timestamp"])
            if index > 0:
                signal = working_signals.iloc[index - 1]
                previous_bar = working_bars.iloc[index - 1]
                if _segment_identity(previous_bar) != _segment_identity(bar):
                    if portfolio.current_position != 0:
                        portfolio.close(
                            timestamp=pd.Timestamp(previous_bar["timestamp"]),
                            raw_price=float(previous_bar["close"]),
                            cost_model=self.cost_model,
                            reason="contract_roll",
                        )
                        equity_rows[-1] = _equity_row(
                            portfolio,
                            pd.Timestamp(previous_bar["timestamp"]),
                            float(previous_bar["close"]),
                            self.cost_model.point_value,
                        )
                    self._record_rejection("segment_boundary")
                else:
                    self._execute_signal(portfolio, signal, bar, trade_counts)
            equity_rows.append(
                _equity_row(portfolio, timestamp, float(bar["close"]), self.cost_model.point_value)
            )

        if portfolio.current_position != 0:
            final_bar = working_bars.iloc[-1]
            portfolio.close(
                timestamp=pd.Timestamp(final_bar["timestamp"]),
                raw_price=float(final_bar["close"]),
                cost_model=self.cost_model,
                reason="end_of_data",
            )
            equity_rows[-1] = _equity_row(
                portfolio,
                pd.Timestamp(final_bar["timestamp"]),
                float(final_bar["close"]),
                self.cost_model.point_value,
            )

        trades = portfolio.trades_frame()
        equity_curve = pd.DataFrame(equity_rows)
        metrics = calculate_metrics(self.initial_cash, trades, equity_curve)
        return BacktestResult(
            trades=trades,
            equity_curve=equity_curve,
            metrics=metrics,
            diagnostics={"execution_rejections": dict(self.rejection_counts)},
        )

    def _execute_signal(
        self,
        portfolio: Portfolio,
        signal: pd.Series,
        execution_bar: pd.Series,
        trade_counts: dict[date, int],
    ) -> None:
        target_position = int(signal["target_position"])
        if target_position == portfolio.current_position:
            return

        execution_time = pd.Timestamp(execution_bar["timestamp"])
        raw_open = float(execution_bar["open"])
        reason = str(signal["reason"])

        if portfolio.current_position != 0:
            portfolio.close(
                timestamp=execution_time,
                raw_price=raw_open,
                cost_model=self.cost_model,
                reason=reason,
            )

        if target_position == 0:
            return
        if abs(target_position) > self.max_position:
            return
        if target_position < 0 and not self.allow_short:
            self._record_rejection("short_disabled")
            return
        required_margin = self.assumed_margin_per_contract
        if required_margin is not None and portfolio.cash < required_margin * abs(target_position):
            self._record_rejection("insufficient_assumed_margin")
            return

        trading_day = execution_time.date()
        if self.max_trades_per_day is not None:
            used_trades = trade_counts.get(trading_day, 0)
            if used_trades >= self.max_trades_per_day:
                return
            trade_counts[trading_day] = used_trades + 1

        side: PositionSide = "LONG" if target_position > 0 else "SHORT"
        portfolio.open(
            timestamp=execution_time,
            symbol=str(signal["symbol"]),
            side=side,
            raw_price=raw_open,
            qty=abs(target_position),
            cost_model=self.cost_model,
        )

    def _record_rejection(self, reason: str) -> None:
        self.rejection_counts[reason] = self.rejection_counts.get(reason, 0) + 1


def run_backtest_from_config(
    config: BacktestConfig,
    *,
    preflight: BacktestPreflight | None = None,
    progress_callback: ProgressCallback | None = None,
) -> BacktestResult:
    """Execute a prepared or freshly calculated conservative backtest."""
    if config.strategy.name != "vwap_trend":
        raise ValueError("Task 8 supports the vwap_trend strategy only")

    reporter = ProgressReporter("backtest", progress_callback)
    if preflight is None:
        prepared = prepare_backtest(config, progress_callback=progress_callback)
        reused = False
    else:
        current_fingerprint = build_data_fingerprint(config)
        if preflight.data_fingerprint != current_fingerprint:
            raise ValueError("Preflight result is stale; run preflight again before backtest")
        prepared = preflight
        reused = True
    timings = dict(prepared.timings)
    timings["preflight_reused"] = float(reused)
    diagnostics = deepcopy(prepared.diagnostics)
    reporter.update("Execute backtest", 0, 1, "Executing next-bar-open simulation")
    started = perf_counter()
    execution = BacktestEngine.from_config(config).run(prepared.model_bars, prepared.signals)
    timings["backtest_execution"] = perf_counter() - started
    diagnostics["execution_rejections"] = execution.diagnostics.get("execution_rejections", {})
    if execution.metrics.get("trade_count", 0) == 0 and diagnostics["execution_rejections"].get(
        "insufficient_assumed_margin", 0
    ):
        diagnostics["primary_zero_trade_reason"] = "assumed margin insufficient"
    reporter.update("Complete", 1, 1, "Backtest complete")
    return BacktestResult(
        trades=execution.trades,
        equity_curve=execution.equity_curve,
        metrics=execution.metrics,
        model_bars=prepared.model_bars,
        signals=prepared.signals,
        contract_selection=prepared.contract_selection,
        diagnostics=diagnostics,
        timings=timings,
        data_fingerprint=prepared.data_fingerprint,
    )


def prepare_backtest(
    config: BacktestConfig,
    *,
    progress_callback: ProgressCallback | None = None,
) -> BacktestPreflight:
    """Load, select, enrich, and diagnose bars without executing trades."""
    if config.strategy.name != "vwap_trend":
        raise ValueError("V1 supports the vwap_trend strategy only")
    reporter = ProgressReporter("backtest_preflight", progress_callback)
    timings: dict[str, float] = {}
    reporter.update("Load bars", 0, 4, "Loading configured bars")
    started = perf_counter()
    bars = load_configured_bars(config)
    timings["bar_loading"] = perf_counter() - started
    reporter.update("Select contracts", 1, 4, "Selecting active TMF contracts")
    started = perf_counter()
    selection = select_contract_bars(
        bars,
        contract_mode=config.data.contract_mode,
        contract=config.data.contract,
        roll_confirmation_days=config.data.roll_confirmation_days,
    )
    timings["contract_selection"] = perf_counter() - started
    params: dict[str, object] = dict(config.strategy.params)
    reporter.update("Calculate indicators", 2, 4, "Calculating segment-safe indicators")
    started = perf_counter()
    enriched_bars = append_basic_indicators(
        selection.bars,
        ema_fast=_int_param(params, "ema_fast", 20),
        ema_slow=_int_param(params, "ema_slow", 60),
        atr_period=_int_param(params, "atr_period", 14),
        volatility_window=_int_param(params, "volatility_window", 20),
    )
    timings["indicator_calculation"] = perf_counter() - started
    reporter.update("Generate signals", 3, 4, "Generating and aligning strategy signals")
    started = perf_counter()
    signals = VWAPTrendStrategy.from_config_params(params).generate_signals(enriched_bars)
    signals["contract"] = enriched_bars["contract"].to_numpy()
    signals["contract_segment_id"] = enriched_bars["contract_segment_id"].to_numpy()
    _validate_bar_signal_alignment(_prepare_bars(enriched_bars), _prepare_signals(signals))
    timings["signal_generation"] = perf_counter() - started
    diagnostics = build_backtest_diagnostics(bars, selection, enriched_bars, signals, config)
    if diagnostics["errors"]:
        raise ValueError("Backtest preflight failed: " + "; ".join(diagnostics["errors"]))
    fingerprint = build_data_fingerprint(config)
    reporter.update("Complete", 4, 4, "Backtest preflight complete")
    return BacktestPreflight(
        model_bars=enriched_bars,
        signals=signals,
        contract_selection=selection.audit,
        diagnostics=diagnostics,
        timings=timings,
        data_fingerprint=fingerprint,
    )


def load_configured_bars(config: BacktestConfig) -> pd.DataFrame:
    """Load and filter daily bar Parquet files selected by config."""
    bar_files = discover_bar_files(
        config.data.processed_dir,
        symbol=config.data.symbol,
        timeframe=config.data.timeframe,
        start_date=config.data.start_date,
        end_date=config.data.end_date,
    )
    if not bar_files:
        raise FileNotFoundError(
            "No bar Parquet files found for configured range. "
            "Run `tifq build-bars` before `tifq backtest`."
        )

    bars = pd.concat([read_parquet(path) for path in bar_files], ignore_index=True)
    validate_bar_frame(bars)
    timestamps = pd.to_datetime(bars["timestamp"])
    selected = bars.loc[
        (timestamps.dt.date >= config.data.start_date)
        & (timestamps.dt.date <= config.data.end_date)
    ]
    if selected.empty:
        raise ValueError("No bars remain after applying configured date range")
    return selected.sort_values("timestamp", kind="mergesort").reset_index(drop=True)


def discover_bar_files(
    processed_dir: str | Path,
    *,
    symbol: str = V1_SYMBOL,
    timeframe: str,
    start_date: date,
    end_date: date,
) -> list[Path]:
    """Return sorted bar Parquet files in the V1 processed layout."""
    bar_dir = Path(processed_dir) / "bars" / symbol / timeframe
    if not bar_dir.exists():
        return []
    if not bar_dir.is_dir():
        raise NotADirectoryError(f"Bar path is not a directory: {bar_dir}")

    paths: list[Path] = []
    for path in sorted(bar_dir.iterdir()):
        if not path.is_file() or path.suffix != ".parquet":
            continue
        try:
            file_date = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if start_date <= file_date <= end_date:
            paths.append(path)
    return paths


def _prepare_bars(bars: pd.DataFrame) -> pd.DataFrame:
    validate_bar_frame(bars)
    if bars.empty:
        raise ValueError("bars must not be empty")
    prepared = bars.copy().sort_values("timestamp", kind="mergesort").reset_index(drop=True)
    _require_identity_columns(prepared, "bars")
    if pd.to_datetime(prepared["timestamp"]).duplicated().any():
        raise ValueError("bars contain duplicate active timestamps")
    return prepared


def _prepare_signals(signals: pd.DataFrame) -> pd.DataFrame:
    validate_signal_frame(signals)
    if signals.empty:
        raise ValueError("signals must not be empty")
    prepared = signals.copy().sort_values("timestamp", kind="mergesort").reset_index(drop=True)
    _require_identity_columns(prepared, "signals")
    return prepared


IDENTITY_COLUMNS = ("timestamp", "symbol", "contract", "contract_segment_id")


def _require_identity_columns(frame: pd.DataFrame, frame_name: str) -> None:
    missing = [column for column in IDENTITY_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"{frame_name} missing identity columns: {', '.join(missing)}")


def _validate_bar_signal_alignment(bars: pd.DataFrame, signals: pd.DataFrame) -> None:
    for column in IDENTITY_COLUMNS:
        bar_values = bars[column]
        signal_values = signals[column]
        if column == "timestamp":
            aligned = pd.to_datetime(bar_values).equals(pd.to_datetime(signal_values))
        else:
            aligned = bar_values.astype(str).equals(signal_values.astype(str))
        if not aligned:
            raise ValueError(f"bars and signals {column} must align exactly")


def _segment_identity(row: pd.Series) -> tuple[str, str, str]:
    return (
        str(row["symbol"]),
        str(row["contract"]),
        str(row["contract_segment_id"]),
    )


def _equity_row(
    portfolio: Portfolio,
    timestamp: pd.Timestamp,
    close_price: float,
    point_value: float,
) -> dict[str, Any]:
    equity = portfolio.mark_to_market(close_price, point_value)
    return {
        "timestamp": timestamp,
        "cash": portfolio.cash,
        "position": portfolio.current_position,
        "close": close_price,
        "equity": equity,
    }


def _int_param(params: dict[str, object], name: str, default: int) -> int:
    value = params.get(name, default)
    if isinstance(value, str | int):
        return int(value)
    raise ValueError(f"{name} must be an integer; got: {value}")


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, str | int):
        return int(value)
    raise ValueError(f"max_trades_per_day must be an integer; got: {value}")


def build_data_fingerprint(config: BacktestConfig) -> dict[str, Any]:
    paths = discover_bar_files(
        config.data.processed_dir,
        symbol=config.data.symbol,
        timeframe=config.data.timeframe,
        start_date=config.data.start_date,
        end_date=config.data.end_date,
    )
    manifest_path = config.data.processed_dir / "bar_manifest.json"
    manifest_stat = manifest_path.stat() if manifest_path.exists() else None
    return {
        "config_json": config.model_dump_json(),
        "source_bar_paths": [str(path.resolve()) for path in paths],
        "source_hashes": {str(path.resolve()): sha256_file(path) for path in paths},
        "source_metadata": {
            str(path.resolve()): {
                "size": path.stat().st_size,
                "mtime_ns": path.stat().st_mtime_ns,
            }
            for path in paths
        },
        "bar_manifest": {
            "path": str(manifest_path.resolve()),
            "size": manifest_stat.st_size if manifest_stat else None,
            "mtime_ns": manifest_stat.st_mtime_ns if manifest_stat else None,
            "sha256": sha256_file(manifest_path) if manifest_stat else None,
        },
        "contract_mode": config.data.contract_mode,
        "selected_contract": config.data.contract,
        "indicator_params": {
            key: config.strategy.params.get(key)
            for key in ("ema_fast", "ema_slow", "atr_period", "volatility_window")
        },
        "strategy_params": dict(config.strategy.params),
        "cost_params": config.cost.model_dump(mode="json"),
        "package_version": "0.1.0",
    }
