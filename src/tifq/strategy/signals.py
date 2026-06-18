"""Structured strategy signal schema."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

SignalSide = Literal["BUY", "SELL", "FLAT", "HOLD"]

SIGNAL_REQUIRED_COLUMNS: tuple[str, ...] = (
    "timestamp",
    "symbol",
    "side",
    "target_position",
    "reason",
    "stop_loss",
    "take_profit",
)


@dataclass(frozen=True)
class Signal:
    """Structured strategy signal emitted after a bar closes."""

    timestamp: pd.Timestamp
    symbol: str
    side: SignalSide
    target_position: int
    reason: str
    stop_loss: float | None = None
    take_profit: float | None = None


def signals_to_frame(signals: list[Signal]) -> pd.DataFrame:
    """Convert strategy signals into a stable DataFrame schema."""
    records = [
        {
            "timestamp": signal.timestamp,
            "symbol": signal.symbol,
            "side": signal.side,
            "target_position": signal.target_position,
            "reason": signal.reason,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
        }
        for signal in signals
    ]
    return pd.DataFrame(records, columns=list(SIGNAL_REQUIRED_COLUMNS))


def validate_signal_frame(signals: pd.DataFrame) -> None:
    """Validate the required signal columns and side values."""
    missing = [column for column in SIGNAL_REQUIRED_COLUMNS if column not in signals.columns]
    if missing:
        raise ValueError(f"signal frame missing required columns: {', '.join(missing)}")
    invalid_sides = sorted(
        side
        for side in set(signals["side"].astype(str))
        if side not in {"BUY", "SELL", "FLAT", "HOLD"}
    )
    if invalid_sides:
        raise ValueError(f"signal frame contains invalid sides: {', '.join(invalid_sides)}")
