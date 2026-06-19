"""Backtest preflight integrity checks and explainable zero-trade diagnostics."""

from __future__ import annotations

from datetime import time
from typing import Any

import pandas as pd

from tifq.backtest.contracts import ContractSelectionResult
from tifq.config.models import BacktestConfig


def build_backtest_diagnostics(
    raw_bars: pd.DataFrame,
    selection: ContractSelectionResult,
    model_bars: pd.DataFrame,
    signals: pd.DataFrame,
    config: BacktestConfig,
) -> dict[str, Any]:
    """Return integrity statistics, blockers, warnings, and a factual primary reason."""
    errors: list[str] = []
    warnings: list[str] = []
    selected = selection.bars
    timestamps = pd.to_datetime(selected["timestamp"])
    if selected.empty:
        errors.append("selected bars are empty")
    if timestamps.duplicated().any():
        errors.append("duplicate active timestamps")
    if not timestamps.is_monotonic_increasing:
        errors.append("timestamps are not monotonically increasing")
    if selected[["open", "high", "low", "close", "volume"]].isna().any().any():
        errors.append("OHLCV contains NaN")
    if (selected[["open", "high", "low", "close"]] <= 0).any().any():
        errors.append("OHLC prices must be positive")
    if (selected["volume"] < 0).any():
        errors.append("volume must be non-negative")
    if len(model_bars) != len(signals):
        errors.append("model bars and signals have different row counts")
    elif not pd.to_datetime(model_bars["timestamp"]).reset_index(drop=True).equals(
        pd.to_datetime(signals["timestamp"]).reset_index(drop=True)
    ):
        errors.append("model bars and signals timestamps are not aligned")

    params = config.strategy.params
    no_entry_before = _time_param(params.get("no_entry_before"), time(8, 55))
    no_entry_after = _time_param(params.get("no_entry_after"), time(13, 20))
    min_atr = _float_param(params.get("min_atr_points"), 10.0)
    max_atr = _float_param(params.get("max_atr_points"), 120.0)
    bar_times = pd.to_datetime(model_bars["timestamp"]).dt.time
    entry_window = bar_times.map(lambda value: no_entry_before <= value <= no_entry_after)
    indicator_valid = model_bars[["ema_fast", "ema_slow", "vwap", "atr"]].notna().all(axis=1)
    atr_valid = model_bars["atr"].notna()
    atr_in_range = atr_valid & model_bars["atr"].between(min_atr, max_atr)
    previous_close = model_bars["close"].shift(1)
    previous_fast = model_bars["ema_fast"].shift(1)
    same_segment = model_bars["contract_segment_id"].eq(
        model_bars["contract_segment_id"].shift(1)
    )
    long_trend = model_bars["ema_fast"] > model_bars["ema_slow"]
    short_trend = model_bars["ema_fast"] < model_bars["ema_slow"]
    long_vwap = model_bars["close"] > model_bars["vwap"]
    short_vwap = model_bars["close"] < model_bars["vwap"]
    long_cross = same_segment & previous_close.le(previous_fast) & model_bars["close"].gt(
        model_bars["ema_fast"]
    )
    short_cross = same_segment & previous_close.ge(previous_fast) & model_bars["close"].lt(
        model_bars["ema_fast"]
    )
    eligible = entry_window & indicator_valid & atr_in_range
    long_candidates = eligible & long_trend & long_vwap & long_cross
    short_candidates = eligible & short_trend & short_vwap & short_cross
    price_jumps = model_bars.groupby("contract_segment_id", sort=False)["close"].pct_change().abs()
    extreme_jumps = int((price_jumps > 0.2).sum())
    if extreme_jumps:
        warnings.append(f"{extreme_jumps} price jumps exceed 20% within a contract segment")
    if float(atr_valid.mean()) < 0.5:
        warnings.append("ATR valid ratio is below 50%")
    if int(entry_window.sum()) < 10:
        warnings.append("fewer than 10 bars are inside the entry window")
    if int(long_candidates.sum() + short_candidates.sum()) == 0:
        warnings.append("strategy entry candidates are zero")
    if config.portfolio.assumed_margin_per_contract is None:
        warnings.append("assumed margin is not configured")

    signal_counts = signals["side"].value_counts().to_dict()
    stats: dict[str, Any] = {
        "date_range": {
            "start": timestamps.min().date().isoformat(),
            "end": timestamps.max().date().isoformat(),
        },
        "bar_count": len(model_bars),
        "trading_days": int(timestamps.dt.date.nunique()),
        "original_contracts": list(selection.original_contracts),
        "selected_contracts": sorted(set(selected["contract"].astype(str))),
        "active_contracts_per_day_max": int(
            selected.assign(trading_date=timestamps.dt.date)
            .groupby("trading_date")["contract"]
            .nunique()
            .max()
        ),
        "duplicate_timestamp_count": int(timestamps.duplicated().sum()),
        "invalid_contracts": list(selection.invalid_contracts),
        "roll_count": int(selection.audit["rolled"].sum()),
        "segment_count": int(selected["contract_segment_id"].nunique()),
        "ema_valid_ratio": float(model_bars[["ema_fast", "ema_slow"]].notna().all(axis=1).mean()),
        "atr_valid_ratio": float(atr_valid.mean()),
        "atr_in_range_ratio": float(atr_in_range.mean()),
        "entry_window_bars": int(entry_window.sum()),
        "long_entry_candidates": int(long_candidates.sum()),
        "short_entry_candidates": int(short_candidates.sum()),
        "buy_signals": int(signal_counts.get("BUY", 0)),
        "sell_signals": int(signal_counts.get("SELL", 0)),
        "flat_signals": int(signal_counts.get("FLAT", 0)),
        "hold_signals": int(signal_counts.get("HOLD", 0)),
        "extreme_price_jump_count": extreme_jumps,
    }
    return {
        "status": "error" if errors else ("warning" if warnings else "healthy"),
        "errors": errors,
        "warnings": warnings,
        "stats": stats,
        "primary_zero_trade_reason": _zero_trade_reason(
            stats,
            entry_window=entry_window,
            indicator_valid=indicator_valid,
            atr_valid=atr_valid,
            model_bars=model_bars,
            min_atr=min_atr,
            max_atr=max_atr,
            long_trend=long_trend,
            short_trend=short_trend,
            long_vwap=long_vwap,
            short_vwap=short_vwap,
            long_cross=long_cross,
            short_cross=short_cross,
            allow_short=config.portfolio.allow_short,
        ),
    }


def _zero_trade_reason(
    stats: dict[str, Any],
    *,
    entry_window: pd.Series,
    indicator_valid: pd.Series,
    atr_valid: pd.Series,
    model_bars: pd.DataFrame,
    min_atr: float,
    max_atr: float,
    long_trend: pd.Series,
    short_trend: pd.Series,
    long_vwap: pd.Series,
    short_vwap: pd.Series,
    long_cross: pd.Series,
    short_cross: pd.Series,
    allow_short: bool,
) -> str | None:
    if stats["buy_signals"] or stats["sell_signals"]:
        return None
    if stats["bar_count"] == 0:
        return "no selected bars"
    if not indicator_valid.any():
        return "indicator warmup"
    if not entry_window.any():
        return "outside entry window"
    eligible_atr = model_bars.loc[entry_window & atr_valid, "atr"]
    if not eligible_atr.empty and (eligible_atr < min_atr).all():
        return "ATR below minimum"
    if not eligible_atr.empty and (eligible_atr > max_atr).all():
        return "ATR above maximum"
    if not (long_trend | short_trend).any():
        return "trend condition false"
    if not (long_vwap | short_vwap).any():
        return "VWAP condition false"
    if not (long_cross | short_cross).any():
        return "EMA crossover absent"
    if not allow_short and stats["long_entry_candidates"] == 0 and stats["short_entry_candidates"]:
        return "short disabled"
    return "entry conditions did not align"


def _time_param(value: object, default: time) -> time:
    if value is None:
        return default
    if isinstance(value, time):
        return value
    return time.fromisoformat(str(value))


def _float_param(value: object, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, str | int | float):
        return float(value)
    raise ValueError(f"expected numeric diagnostic parameter; got: {value}")
