"""YAML loading utilities for V1 backtest configuration."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import yaml
from pydantic import ValidationError

from tifq.config.models import BacktestConfig


class ConfigLoadError(ValueError):
    """Raised when a config file cannot be read as a YAML mapping."""


def load_backtest_config(path: str | Path) -> BacktestConfig:
    """Load and validate a V1 backtest config file."""
    config_path = Path(path)
    try:
        raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigLoadError(f"Could not read config file: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigLoadError(f"Could not parse YAML config file: {config_path}") from exc

    if not isinstance(raw_config, dict):
        raise ConfigLoadError("Backtest config must be a YAML mapping at the document root")

    try:
        return BacktestConfig.model_validate(cast(dict[str, object], raw_config))
    except ValidationError:
        raise

