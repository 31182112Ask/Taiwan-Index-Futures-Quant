from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from tifq.config import ConfigLoadError, load_backtest_config


def valid_config() -> dict[str, Any]:
    return {
        "project": {
            "name": "Taiwan Index Futures Quant",
            "timezone": "Asia/Taipei",
        },
        "data": {
            "symbol": "TMF",
            "contract_mode": "continuous_front_month",
            "raw_dir": "data/raw/taifex",
            "processed_dir": "data/processed",
            "start_date": "2026-05-18",
            "end_date": "2026-06-17",
            "session": "day",
            "timeframe": "5m",
        },
        "product": {
            "point_value": 10,
            "tick_size": 1,
            "exchange": "TAIFEX",
        },
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
                "force_flatten_time": "13:35:00",
            },
        },
        "portfolio": {
            "initial_cash": 100000,
            "max_position": 1,
            "allow_short": True,
        },
    }


def write_config(tmp_path: Path, config: dict[str, Any]) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def test_loads_repository_backtest_config() -> None:
    config = load_backtest_config("configs/v1_backtest.yaml")

    assert config.data.symbol == "TMF"
    assert config.data.timeframe == "5m"
    assert config.data.session == "day"
    assert config.product.point_value == 10
    assert config.product.tick_size == 1
    assert config.portfolio.initial_cash == 100000
    assert config.data.contract is None
    assert config.data.roll_confirmation_days == 1


def test_accepts_path_object_and_defaults_product_values(tmp_path: Path) -> None:
    raw_config = valid_config()
    raw_config["product"] = {"exchange": "TAIFEX"}
    path = write_config(tmp_path, raw_config)

    config = load_backtest_config(path)

    assert config.product.point_value == 10
    assert config.product.tick_size == 1


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("data", "symbol", "TX"),
        ("data", "timeframe", "15m"),
        ("data", "session", "night"),
        ("product", "point_value", 50),
        ("product", "tick_size", 5),
        ("cost", "commission_per_side", -1),
        ("cost", "tax_rate", -0.01),
        ("cost", "slippage_points_per_side", -1),
        ("portfolio", "initial_cash", 0),
        ("portfolio", "max_position", -1),
    ],
)
def test_rejects_invalid_v1_constraints(
    tmp_path: Path,
    section: str,
    key: str,
    value: object,
) -> None:
    raw_config = valid_config()
    raw_config[section][key] = value
    path = write_config(tmp_path, raw_config)

    with pytest.raises(ValidationError):
        load_backtest_config(path)


def test_rejects_start_date_after_end_date(tmp_path: Path) -> None:
    raw_config = valid_config()
    raw_config["data"]["start_date"] = "2026-06-18"
    raw_config["data"]["end_date"] = "2026-06-17"
    path = write_config(tmp_path, raw_config)

    with pytest.raises(ValidationError, match="start_date"):
        load_backtest_config(path)


def test_single_contract_requires_valid_contract(tmp_path: Path) -> None:
    raw_config = valid_config()
    raw_config["data"]["contract_mode"] = "single_contract"
    path = write_config(tmp_path, raw_config)
    with pytest.raises(ValidationError, match="contract"):
        load_backtest_config(path)

    raw_config["data"]["contract"] = "202613"
    path = write_config(tmp_path, raw_config)
    with pytest.raises(ValidationError, match="month"):
        load_backtest_config(path)

    raw_config["data"]["contract"] = "202606"
    assert load_backtest_config(write_config(tmp_path, raw_config)).data.contract == "202606"


def test_continuous_mode_rejects_explicit_contract(tmp_path: Path) -> None:
    raw_config = valid_config()
    raw_config["data"]["contract"] = "202606"

    with pytest.raises(ValidationError, match="must be null"):
        load_backtest_config(write_config(tmp_path, raw_config))


def test_rejects_non_mapping_yaml(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    with pytest.raises(ConfigLoadError, match="YAML mapping"):
        load_backtest_config(path)


def test_missing_file_raises_config_load_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigLoadError, match="Could not read config file"):
        load_backtest_config(tmp_path / "missing.yaml")
