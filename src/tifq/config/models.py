"""Pydantic models for V1 backtest configuration."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

Timeframe: TypeAlias = Literal["1m", "5m"]
Session: TypeAlias = Literal["day"]
Symbol: TypeAlias = Literal["TMF"]
ContractMode: TypeAlias = Literal["single_contract", "continuous_front_month"]
JsonScalar: TypeAlias = str | int | float | bool | None


class StrictModel(BaseModel):
    """Base model that rejects unknown fields in config files."""

    model_config = ConfigDict(extra="forbid")


class ProjectConfig(StrictModel):
    """Project-level settings."""

    name: str
    timezone: str = "Asia/Taipei"


class DataConfig(StrictModel):
    """Historical data selection and storage settings for V1."""

    symbol: Symbol
    contract_mode: ContractMode
    contract: str | None = None
    roll_confirmation_days: int = Field(default=1, ge=1)
    raw_dir: Path
    processed_dir: Path
    start_date: date
    end_date: date
    session: Session
    timeframe: Timeframe

    @model_validator(mode="after")
    def validate_date_range(self) -> DataConfig:
        """Require an ordered inclusive backtest date range."""
        if self.start_date > self.end_date:
            raise ValueError("data.start_date must be earlier than or equal to data.end_date")
        if self.contract_mode == "single_contract":
            if self.contract is None:
                raise ValueError("data.contract is required for single_contract mode")
            self.contract = _normalize_month_contract(self.contract)
        elif self.contract is not None:
            raise ValueError("data.contract must be null for continuous_front_month mode")
        return self


class ProductConfig(StrictModel):
    """TMF contract settings supported in V1."""

    point_value: int = 10
    tick_size: int = 1
    exchange: str = "TAIFEX"

    @model_validator(mode="after")
    def validate_tmf_defaults(self) -> ProductConfig:
        """V1 is scoped to TMF only, with fixed point value and tick size."""
        if self.point_value != 10:
            raise ValueError("product.point_value must be 10 for TMF in V1")
        if self.tick_size != 1:
            raise ValueError("product.tick_size must be 1 for TMF in V1")
        return self


class CostConfig(StrictModel):
    """Execution cost assumptions."""

    commission_per_side: float = Field(ge=0)
    tax_rate: float = Field(ge=0)
    slippage_points_per_side: float = Field(ge=0)


class StrategyConfig(StrictModel):
    """Strategy selection and parameter values."""

    name: str
    params: dict[str, JsonScalar] = Field(default_factory=dict)


class PortfolioConfig(StrictModel):
    """Portfolio constraints for local historical testing."""

    initial_cash: float = Field(gt=0)
    max_position: int = Field(ge=0)
    allow_short: bool = True
    assumed_margin_per_contract: float | None = Field(default=None, gt=0)


class BacktestConfig(StrictModel):
    """Top-level V1 backtest configuration."""

    project: ProjectConfig
    data: DataConfig
    product: ProductConfig = Field(default_factory=ProductConfig)
    cost: CostConfig
    strategy: StrategyConfig
    portfolio: PortfolioConfig


def _normalize_month_contract(value: str) -> str:
    normalized = value.strip()
    if not re.fullmatch(r"\d{6}", normalized):
        raise ValueError("data.contract must use YYYYMM format")
    month = int(normalized[4:])
    if not 1 <= month <= 12:
        raise ValueError("data.contract month must be between 01 and 12")
    return normalized
