"""Strategy interfaces and signal generators."""

from tifq.strategy.base import BaseStrategy
from tifq.strategy.signals import (
    SIGNAL_REQUIRED_COLUMNS,
    Signal,
    SignalSide,
    signals_to_frame,
    validate_signal_frame,
)
from tifq.strategy.vwap_trend import VWAPTrendParams, VWAPTrendStrategy

__all__ = [
    "SIGNAL_REQUIRED_COLUMNS",
    "BaseStrategy",
    "Signal",
    "SignalSide",
    "VWAPTrendParams",
    "VWAPTrendStrategy",
    "signals_to_frame",
    "validate_signal_frame",
]

