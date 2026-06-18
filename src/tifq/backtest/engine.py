"""Conservative next-bar-open historical backtest engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from tifq.backtest.cost import CostModel
from tifq.backtest.portfolio import Portfolio, PositionSide
from tifq.config.models import BacktestConfig
from tifq.data.schemas import V1_SYMBOL, validate_bar_frame
from tifq.data.storage import read_parquet
from tifq.indicators import append_basic_indicators
from tifq.strategy.signals import validate_signal_frame
from tifq.strategy.vwap_trend import VWAPTrendStrategy


@dataclass(frozen=True)
class BacktestResult:
    """In-memory result of a V1 backtest execution."""

    trades: pd.DataFrame
    equity_curve: pd.DataFrame
    metrics: dict[str, float | int]


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
        )

    def run(self, bars: pd.DataFrame, signals: pd.DataFrame) -> BacktestResult:
        """Run a backtest where signal i executes at bar i+1 open."""
        working_bars = _prepare_bars(bars)
        working_signals = _prepare_signals(signals)
        if len(working_bars) != len(working_signals):
            raise ValueError("bars and signals must have the same number of rows")

        portfolio = Portfolio(self.initial_cash)
        trade_counts: dict[date, int] = {}
        equity_rows: list[dict[str, Any]] = []

        for index, (_, bar) in enumerate(working_bars.iterrows()):
            timestamp = pd.Timestamp(bar["timestamp"])
            if index > 0:
                signal = working_signals.iloc[index - 1]
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
        metrics = _basic_metrics(self.initial_cash, trades, equity_curve)
        return BacktestResult(trades=trades, equity_curve=equity_curve, metrics=metrics)

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


def run_backtest_from_config(config: BacktestConfig) -> BacktestResult:
    """Load configured bars, calculate indicators, run strategy, and execute signals."""
    if config.strategy.name != "vwap_trend":
        raise ValueError("Task 8 supports the vwap_trend strategy only")

    bars = load_configured_bars(config)
    params: dict[str, object] = dict(config.strategy.params)
    enriched_bars = append_basic_indicators(
        bars,
        ema_fast=_int_param(params, "ema_fast", 20),
        ema_slow=_int_param(params, "ema_slow", 60),
        atr_period=_int_param(params, "atr_period", 14),
        volatility_window=_int_param(params, "volatility_window", 20),
    )
    strategy = VWAPTrendStrategy.from_config_params(params)
    signals = strategy.generate_signals(enriched_bars)
    return BacktestEngine.from_config(config).run(enriched_bars, signals)


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
    return bars.copy().sort_values("timestamp", kind="mergesort").reset_index(drop=True)


def _prepare_signals(signals: pd.DataFrame) -> pd.DataFrame:
    validate_signal_frame(signals)
    if signals.empty:
        raise ValueError("signals must not be empty")
    return signals.copy().sort_values("timestamp", kind="mergesort").reset_index(drop=True)


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


def _basic_metrics(
    initial_cash: float,
    trades: pd.DataFrame,
    equity_curve: pd.DataFrame,
) -> dict[str, float | int]:
    final_equity = (
        float(equity_curve.iloc[-1]["equity"]) if not equity_curve.empty else initial_cash
    )
    if trades.empty:
        total_fee = 0.0
        total_tax = 0.0
        total_slippage = 0.0
        trade_count = 0
    else:
        total_fee = float(trades["fee"].sum())
        total_tax = float(trades["tax"].sum())
        total_slippage = float(trades["slippage"].sum())
        trade_count = len(trades)
    return {
        "initial_cash": float(initial_cash),
        "final_equity": final_equity,
        "net_pnl": final_equity - initial_cash,
        "return_pct": (final_equity - initial_cash) / initial_cash,
        "trade_count": trade_count,
        "total_fee": total_fee,
        "total_tax": total_tax,
        "total_slippage": total_slippage,
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
