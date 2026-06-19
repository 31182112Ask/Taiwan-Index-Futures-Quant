"""Deterministic V1 TMF contract selection and roll audit."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

import pandas as pd

from tifq.config.models import ContractMode
from tifq.data.schemas import validate_bar_frame
from tifq.runtime.progress import ProgressCallback, ProgressReporter

SelectionReason = Literal[
    "single_contract",
    "initial_front_month",
    "kept_current",
    "confirmed_volume_roll",
    "current_contract_missing",
]
AUDIT_COLUMNS = (
    "trading_date",
    "selected_contract",
    "selection_reason",
    "current_volume",
    "next_contract",
    "next_volume",
    "rolled",
    "contract_segment_id",
)


@dataclass(frozen=True)
class ContractSelectionResult:
    """Selected one-contract market sequence and reproducible daily audit."""

    bars: pd.DataFrame
    audit: pd.DataFrame
    invalid_contracts: tuple[str, ...]
    original_contracts: tuple[str, ...]


def normalize_month_contract(value: object) -> str:
    """Return canonical YYYYMM or raise for weekly/malformed contracts."""
    normalized = str(value).strip()
    if not re.fullmatch(r"\d{6}", normalized):
        raise ValueError(f"unsupported TMF contract format: {value}")
    if not 1 <= int(normalized[4:]) <= 12:
        raise ValueError(f"invalid TMF contract month: {value}")
    return normalized


def select_contract_bars(
    bars: pd.DataFrame,
    *,
    contract_mode: ContractMode,
    contract: str | None = None,
    roll_confirmation_days: int = 1,
    progress_callback: ProgressCallback | None = None,
) -> ContractSelectionResult:
    """Select one deterministic active contract per day without future-day data."""
    validate_bar_frame(bars)
    if roll_confirmation_days < 1:
        raise ValueError("roll_confirmation_days must be at least 1")
    working = bars.copy()
    working["timestamp"] = pd.to_datetime(working["timestamp"])
    working["trading_date"] = working["timestamp"].dt.date
    original_contracts = tuple(sorted(set(working["contract"].astype(str))))
    normalized: list[str | None] = []
    invalid: set[str] = set()
    for value in working["contract"]:
        try:
            normalized.append(normalize_month_contract(value))
        except ValueError:
            normalized.append(None)
            invalid.add(str(value))
    working["contract"] = normalized
    valid = working.loc[working["contract"].notna()].copy()
    if valid.empty:
        raise ValueError("No parseable TMF monthly contracts remain after validation")

    reporter = ProgressReporter("contract_selection", progress_callback)
    trading_days = sorted(set(valid["trading_date"]))
    reporter.update("Select contracts", 0, len(trading_days), "Preparing contract volumes")
    if contract_mode == "single_contract":
        result = _select_single(valid, normalize_month_contract(contract), trading_days, reporter)
    elif contract_mode == "continuous_front_month":
        if contract is not None:
            raise ValueError("contract must be null for continuous_front_month mode")
        result = _select_continuous(valid, trading_days, roll_confirmation_days, reporter)
    else:
        raise ValueError(f"unsupported contract mode: {contract_mode}")
    _validate_selected_sequence(result.bars)
    reporter.update("Complete", len(trading_days), len(trading_days), "Contract selection complete")
    return ContractSelectionResult(
        bars=result.bars,
        audit=result.audit,
        invalid_contracts=tuple(sorted(invalid)),
        original_contracts=original_contracts,
    )


def _select_single(
    bars: pd.DataFrame,
    contract: str,
    trading_days: list[date],
    reporter: ProgressReporter,
) -> ContractSelectionResult:
    selected = bars.loc[bars["contract"] == contract].copy()
    if selected.empty:
        raise ValueError(f"Configured single contract {contract} is absent from the date range")
    selected["contract_segment_id"] = "segment_001"
    audit_rows: list[dict[str, object]] = []
    for index, trading_day in enumerate(sorted(set(selected["trading_date"])), start=1):
        day = selected.loc[selected["trading_date"] == trading_day]
        audit_rows.append(
            _audit_row(
                trading_day,
                contract,
                "single_contract",
                float(day["volume"].sum()),
                None,
                0.0,
                False,
                "segment_001",
            )
        )
        reporter.update("Select contracts", index, len(trading_days), str(trading_day))
    return ContractSelectionResult(
        _finalize_selected(selected),
        pd.DataFrame(audit_rows, columns=AUDIT_COLUMNS),
        (),
        (),
    )


def _select_continuous(
    bars: pd.DataFrame,
    trading_days: list[date],
    confirmation_days: int,
    reporter: ProgressReporter,
) -> ContractSelectionResult:
    selected_frames: list[pd.DataFrame] = []
    audit_rows: list[dict[str, object]] = []
    current: str | None = None
    confirmation_count = 0
    segment = 1
    for index, trading_day in enumerate(trading_days, start=1):
        day = bars.loc[bars["trading_date"] == trading_day]
        volumes = day.groupby("contract", sort=True)["volume"].sum().astype(float).to_dict()
        available = sorted(str(value) for value in volumes)
        rolled = False
        next_contract: str | None = None
        next_volume = 0.0
        current_volume = 0.0
        reason: SelectionReason

        if current is None:
            current = _initial_front_month(available, trading_day)
            reason = "initial_front_month"
        elif current not in volumes:
            later = [candidate for candidate in available if candidate > current]
            if not later:
                raise ValueError(
                    f"No later contract available after missing {current} on {trading_day}"
                )
            current = later[0]
            segment += 1
            rolled = True
            confirmation_count = 0
            reason = "current_contract_missing"
        else:
            current_volume = float(volumes[current])
            later = [candidate for candidate in available if candidate > current]
            next_contract = later[0] if later else None
            next_volume = float(volumes.get(next_contract, 0.0)) if next_contract else 0.0
            if next_contract is not None and next_volume > current_volume:
                confirmation_count += 1
            else:
                confirmation_count = 0
            if next_contract is not None and confirmation_count >= confirmation_days:
                current = next_contract
                segment += 1
                rolled = True
                confirmation_count = 0
                reason = "confirmed_volume_roll"
            else:
                reason = "kept_current"

        segment_id = f"segment_{segment:03d}"
        selected_day = day.loc[day["contract"] == current].copy()
        if selected_day.empty:
            raise ValueError(f"Selected contract {current} has no bars on {trading_day}")
        selected_day["contract_segment_id"] = segment_id
        selected_frames.append(selected_day)
        audit_rows.append(
            _audit_row(
                trading_day,
                current,
                reason,
                current_volume or float(volumes.get(current, 0.0)),
                next_contract,
                next_volume,
                rolled,
                segment_id,
            )
        )
        reporter.update("Select contracts", index, len(trading_days), str(trading_day))
    return ContractSelectionResult(
        _finalize_selected(pd.concat(selected_frames, ignore_index=True)),
        pd.DataFrame(audit_rows, columns=AUDIT_COLUMNS),
        (),
        (),
    )


def _initial_front_month(contracts: list[str], trading_day: date) -> str:
    current_month = trading_day.year * 100 + trading_day.month
    eligible = [contract for contract in contracts if int(contract) >= current_month]
    if not eligible:
        raise ValueError(f"No unexpired monthly contract available on {trading_day}")
    return min(eligible)


def _finalize_selected(bars: pd.DataFrame) -> pd.DataFrame:
    return bars.drop(columns="trading_date").sort_values("timestamp", kind="mergesort").reset_index(
        drop=True
    )


def _validate_selected_sequence(bars: pd.DataFrame) -> None:
    if bars.empty:
        raise ValueError("contract selection produced no bars")
    timestamps = pd.to_datetime(bars["timestamp"])
    if timestamps.duplicated().any():
        raise ValueError("contract selection produced duplicate active timestamps")
    if not timestamps.is_monotonic_increasing:
        raise ValueError("contract selection timestamps must be monotonically increasing")
    daily_contracts = bars.assign(trading_date=timestamps.dt.date).groupby("trading_date")[
        "contract"
    ].nunique()
    if (daily_contracts > 1).any():
        raise ValueError("contract selection produced multiple active contracts in one day")


def _audit_row(
    trading_day: date,
    selected_contract: str,
    reason: SelectionReason,
    current_volume: float,
    next_contract: str | None,
    next_volume: float,
    rolled: bool,
    segment_id: str,
) -> dict[str, object]:
    return {
        "trading_date": trading_day,
        "selected_contract": selected_contract,
        "selection_reason": reason,
        "current_volume": current_volume,
        "next_contract": next_contract,
        "next_volume": next_volume,
        "rolled": rolled,
        "contract_segment_id": segment_id,
    }
