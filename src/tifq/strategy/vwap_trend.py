"""VWAP Trend signal generator for V1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from math import isnan

import pandas as pd

from tifq.data.schemas import V1_SYMBOL
from tifq.strategy.base import BaseStrategy
from tifq.strategy.signals import Signal, signals_to_frame, validate_signal_frame

StrategyState = tuple[Signal, int, float | None, float | None, float | None]

REQUIRED_COLUMNS: tuple[str, ...] = (
    "timestamp",
    "symbol",
    "close",
    "ema_fast",
    "ema_slow",
    "vwap",
    "atr",
)


@dataclass(frozen=True)
class VWAPTrendParams:
    """Parameters for VWAP Trend signal generation."""

    atr_stop_mult: float = 1.5
    take_profit_r: float = 1.5
    min_atr_points: float = 10
    max_atr_points: float = 120
    max_trades_per_day: int = 3
    force_flatten_time: time = time(13, 35)
    no_entry_before: time = time(8, 55)
    no_entry_after: time = time(13, 20)


class VWAPTrendStrategy(BaseStrategy):
    """Generate VWAP trend entry and exit signals only."""

    name = "vwap_trend"

    def __init__(self, params: VWAPTrendParams | None = None) -> None:
        self.params = params or VWAPTrendParams()

    @classmethod
    def from_config_params(cls, params: dict[str, object]) -> VWAPTrendStrategy:
        """Create a strategy from YAML strategy params."""
        return cls(
            VWAPTrendParams(
                atr_stop_mult=_float_param(params, "atr_stop_mult", 1.5),
                take_profit_r=_float_param(params, "take_profit_r", 1.5),
                min_atr_points=_float_param(params, "min_atr_points", 10),
                max_atr_points=_float_param(params, "max_atr_points", 120),
                max_trades_per_day=_int_param(params, "max_trades_per_day", 3),
                force_flatten_time=_parse_time(
                    params.get("force_flatten_time", "13:35:00"),
                    "force_flatten_time",
                ),
                no_entry_before=_parse_time(
                    params.get("no_entry_before", "08:55:00"),
                    "no_entry_before",
                ),
                no_entry_after=_parse_time(
                    params.get("no_entry_after", "13:20:00"),
                    "no_entry_after",
                ),
            )
        )

    def generate_signals(self, bars: pd.DataFrame) -> pd.DataFrame:
        """Emit one structured signal row per bar without executing trades."""
        _validate_input_bars(bars)
        working = bars.copy().sort_values("timestamp", kind="mergesort").reset_index(drop=True)

        signals: list[Signal] = []
        current_position = 0
        entry_price: float | None = None
        stop_loss: float | None = None
        take_profit: float | None = None
        trades_for_day = 0
        current_day: object | None = None

        for row_number, (_, row) in enumerate(working.iterrows()):
            timestamp = pd.Timestamp(row["timestamp"])
            trading_day = timestamp.date()
            if current_day != trading_day:
                current_day = trading_day
                trades_for_day = 0
                if current_position != 0:
                    current_position = 0
                    entry_price = None
                    stop_loss = None
                    take_profit = None

            symbol = str(row["symbol"])
            close = float(row["close"])
            atr_value = float(row["atr"])
            previous_row = working.iloc[row_number - 1] if row_number > 0 else None

            signal = _hold_signal(timestamp, symbol, current_position, stop_loss, take_profit)

            if current_position != 0 and timestamp.time() >= self.params.force_flatten_time:
                signal = Signal(
                    timestamp,
                    symbol,
                    "FLAT",
                    0,
                    "force_flatten",
                    stop_loss,
                    take_profit,
                )
                current_position = 0
                entry_price = None
                stop_loss = None
                take_profit = None
            elif current_position == 1:
                signal, current_position, entry_price, stop_loss, take_profit = (
                    self._long_exit_signal(
                        row,
                        previous_row,
                        signal,
                        current_position,
                        entry_price,
                        stop_loss,
                        take_profit,
                    )
                )
            elif current_position == -1:
                signal, current_position, entry_price, stop_loss, take_profit = (
                    self._short_exit_signal(
                        row,
                        previous_row,
                        signal,
                        current_position,
                        entry_price,
                        stop_loss,
                        take_profit,
                    )
                )
            elif (
                trades_for_day < self.params.max_trades_per_day
                and self._can_enter(timestamp.time(), atr_value)
                and previous_row is not None
            ):
                if self._is_long_entry(row, previous_row):
                    stop_loss = close - atr_value * self.params.atr_stop_mult
                    take_profit = close + (close - stop_loss) * self.params.take_profit_r
                    signal = Signal(
                        timestamp,
                        symbol,
                        "BUY",
                        1,
                        "long_entry",
                        stop_loss,
                        take_profit,
                    )
                    current_position = 1
                    entry_price = close
                    trades_for_day += 1
                elif self._is_short_entry(row, previous_row):
                    stop_loss = close + atr_value * self.params.atr_stop_mult
                    take_profit = close - (stop_loss - close) * self.params.take_profit_r
                    signal = Signal(
                        timestamp,
                        symbol,
                        "SELL",
                        -1,
                        "short_entry",
                        stop_loss,
                        take_profit,
                    )
                    current_position = -1
                    entry_price = close
                    trades_for_day += 1

            signals.append(signal)

        result = signals_to_frame(signals)
        validate_signal_frame(result)
        return result

    def _can_enter(self, bar_time: time, atr_value: float) -> bool:
        if isnan(atr_value):
            return False
        return (
            self.params.no_entry_before <= bar_time <= self.params.no_entry_after
            and self.params.min_atr_points <= atr_value <= self.params.max_atr_points
        )

    def _is_long_entry(self, row: pd.Series, previous_row: pd.Series) -> bool:
        return (
            float(row["close"]) > float(row["vwap"])
            and float(row["ema_fast"]) > float(row["ema_slow"])
            and float(previous_row["close"]) <= float(previous_row["ema_fast"])
            and float(row["close"]) > float(row["ema_fast"])
        )

    def _is_short_entry(self, row: pd.Series, previous_row: pd.Series) -> bool:
        return (
            float(row["close"]) < float(row["vwap"])
            and float(row["ema_fast"]) < float(row["ema_slow"])
            and float(previous_row["close"]) >= float(previous_row["ema_fast"])
            and float(row["close"]) < float(row["ema_fast"])
        )

    def _long_exit_signal(
        self,
        row: pd.Series,
        previous_row: pd.Series | None,
        fallback: Signal,
        current_position: int,
        entry_price: float | None,
        stop_loss: float | None,
        take_profit: float | None,
    ) -> StrategyState:
        timestamp = pd.Timestamp(row["timestamp"])
        symbol = str(row["symbol"])
        close = float(row["close"])
        if stop_loss is not None and close <= stop_loss:
            return _flat_result(timestamp, symbol, "stop_loss", stop_loss, take_profit)
        if take_profit is not None and close >= take_profit:
            return _flat_result(timestamp, symbol, "take_profit", stop_loss, take_profit)
        if previous_row is not None and self._is_short_entry(row, previous_row):
            return _flat_result(timestamp, symbol, "reverse_short", stop_loss, take_profit)
        return fallback, current_position, entry_price, stop_loss, take_profit

    def _short_exit_signal(
        self,
        row: pd.Series,
        previous_row: pd.Series | None,
        fallback: Signal,
        current_position: int,
        entry_price: float | None,
        stop_loss: float | None,
        take_profit: float | None,
    ) -> StrategyState:
        timestamp = pd.Timestamp(row["timestamp"])
        symbol = str(row["symbol"])
        close = float(row["close"])
        if stop_loss is not None and close >= stop_loss:
            return _flat_result(timestamp, symbol, "stop_loss", stop_loss, take_profit)
        if take_profit is not None and close <= take_profit:
            return _flat_result(timestamp, symbol, "take_profit", stop_loss, take_profit)
        if previous_row is not None and self._is_long_entry(row, previous_row):
            return _flat_result(timestamp, symbol, "reverse_long", stop_loss, take_profit)
        return fallback, current_position, entry_price, stop_loss, take_profit


def _hold_signal(
    timestamp: pd.Timestamp,
    symbol: str,
    current_position: int,
    stop_loss: float | None,
    take_profit: float | None,
) -> Signal:
    return Signal(timestamp, symbol, "HOLD", current_position, "hold", stop_loss, take_profit)


def _flat_result(
    timestamp: pd.Timestamp,
    symbol: str,
    reason: str,
    stop_loss: float | None,
    take_profit: float | None,
) -> StrategyState:
    return (
        Signal(timestamp, symbol, "FLAT", 0, reason, stop_loss, take_profit),
        0,
        None,
        None,
        None,
    )


def _validate_input_bars(bars: pd.DataFrame) -> None:
    missing = [column for column in REQUIRED_COLUMNS if column not in bars.columns]
    if missing:
        raise ValueError(f"VWAPTrendStrategy bars missing required columns: {', '.join(missing)}")
    invalid_symbols = sorted(
        symbol for symbol in set(bars["symbol"].astype(str)) if symbol != V1_SYMBOL
    )
    if invalid_symbols:
        raise ValueError(f"V1 supports symbol {V1_SYMBOL} only; got: {', '.join(invalid_symbols)}")


def _parse_time(value: object, field_name: str) -> time:
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        try:
            return time.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be HH:MM:SS; got: {value}") from exc
    raise ValueError(f"{field_name} must be a time or HH:MM:SS string")


def _float_param(params: dict[str, object], name: str, default: float) -> float:
    value = params.get(name, default)
    if isinstance(value, str | int | float):
        return float(value)
    raise ValueError(f"{name} must be numeric; got: {value}")


def _int_param(params: dict[str, object], name: str, default: int) -> int:
    value = params.get(name, default)
    if isinstance(value, str | int):
        return int(value)
    raise ValueError(f"{name} must be an integer; got: {value}")
