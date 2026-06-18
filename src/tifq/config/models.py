"""Pydantic models for V1 backtest configuration."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

Timeframe: TypeAlias = Literal["1m", "5m"]
Session: TypeAlias = Literal["day"]
Symbol: TypeAlias = Literal["TMF"]
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
    contract_mode: str
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


class BacktestConfig(StrictModel):
    """Top-level V1 backtest configuration."""

    project: ProjectConfig
    data: DataConfig
    product: ProductConfig = Field(default_factory=ProductConfig)
    cost: CostConfig
    strategy: StrategyConfig
    portfolio: PortfolioConfig

