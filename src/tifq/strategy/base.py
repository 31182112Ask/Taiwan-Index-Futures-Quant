"""Base strategy interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class BaseStrategy(ABC):
    """Strategy interface for pure signal generation."""

    name: str

    @abstractmethod
    def generate_signals(self, bars: pd.DataFrame) -> pd.DataFrame:
        """Emit structured signals from indicator-enriched bars."""

