from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd
import pytest

from tifq.backtest.contracts import select_contract_bars
from tifq.indicators import append_basic_indicators
from tifq.strategy.vwap_trend import VWAPTrendStrategy


def bars(rows: Iterable[tuple[str, str, float, float]]) -> pd.DataFrame:
    records = []
    for timestamp, contract, close, volume in rows:
        records.append(
            {
                "symbol": "TMF",
                "contract": contract,
                "timeframe": "5m",
                "timestamp": pd.Timestamp(timestamp, tz="Asia/Taipei"),
                "open": close,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": volume,
            }
        )
    return pd.DataFrame(records)


def test_single_day_multiple_contracts_selects_one_front_month() -> None:
    frame = bars(
        [
            ("2026-06-17 08:45", "202606", 100, 10),
            ("2026-06-17 08:45", "202607", 200, 50),
            ("2026-06-17 08:45", "202608", 300, 70),
        ]
    )

    result = select_contract_bars(frame, contract_mode="continuous_front_month")

    assert result.bars["contract"].tolist() == ["202606"]
    assert result.bars["timestamp"].is_unique
    assert result.audit.loc[0, "selection_reason"] == "initial_front_month"


def test_single_contract_filters_exactly_and_missing_contract_errors() -> None:
    frame = bars(
        [
            ("2026-06-17 08:45", "202606", 100, 10),
            ("2026-06-17 08:45", "202607", 200, 20),
        ]
    )

    selected = select_contract_bars(
        frame,
        contract_mode="single_contract",
        contract="202607",
    )

    assert set(selected.bars["contract"]) == {"202607"}
    with pytest.raises(ValueError, match="absent"):
        select_contract_bars(frame, contract_mode="single_contract", contract="202608")


def test_continuous_contract_rolls_only_after_confirmation() -> None:
    frame = bars(
        [
            ("2026-06-17 08:45", "202606", 100, 100),
            ("2026-06-17 08:45", "202607", 200, 10),
            ("2026-06-18 08:45", "202606", 101, 20),
            ("2026-06-18 08:45", "202607", 201, 30),
            ("2026-06-19 08:45", "202606", 102, 20),
            ("2026-06-19 08:45", "202607", 202, 40),
            ("2026-06-22 08:45", "202606", 103, 20),
            ("2026-06-22 08:45", "202607", 203, 40),
        ]
    )

    result = select_contract_bars(
        frame,
        contract_mode="continuous_front_month",
        roll_confirmation_days=2,
    )

    assert result.bars["contract"].tolist() == ["202606", "202606", "202606", "202607"]
    assert result.audit["rolled"].tolist() == [False, False, False, True]
    assert result.audit["contract_segment_id"].tolist() == [
        "segment_001",
        "segment_001",
        "segment_001",
        "segment_002",
    ]


def test_missing_current_contract_rolls_forward_but_never_backward() -> None:
    frame = bars(
        [
            ("2026-06-17 08:45", "202606", 100, 100),
            ("2026-06-18 08:45", "202605", 90, 500),
            ("2026-06-18 08:45", "202607", 200, 10),
        ]
    )

    result = select_contract_bars(frame, contract_mode="continuous_front_month")

    assert result.bars["contract"].tolist() == ["202606", "202607"]
    assert result.audit.loc[1, "selection_reason"] == "current_contract_missing"


def test_future_day_volume_does_not_change_past_selection() -> None:
    first_day = bars(
        [
            ("2026-06-17 08:45", "202606", 100, 100),
            ("2026-06-17 08:45", "202607", 200, 1),
        ]
    )
    with_future = pd.concat(
        [
            first_day,
            bars(
                [
                    ("2026-06-18 08:45", "202606", 101, 1),
                    ("2026-06-18 08:45", "202607", 201, 10_000),
                ]
            ),
        ],
        ignore_index=True,
    )

    truncated = select_contract_bars(first_day, contract_mode="continuous_front_month")
    full = select_contract_bars(with_future, contract_mode="continuous_front_month")

    assert truncated.audit.loc[0, "selected_contract"] == full.audit.loc[0, "selected_contract"]


def test_current_day_volume_cannot_change_same_day_selection() -> None:
    frame = bars(
        [
            ("2026-06-17 08:45", "202606", 100, 100),
            ("2026-06-17 08:45", "202607", 200, 10),
            ("2026-06-18 08:45", "202606", 101, 1),
            ("2026-06-18 08:45", "202607", 201, 10_000),
        ]
    )

    result = select_contract_bars(frame, contract_mode="continuous_front_month")

    assert result.audit["selected_contract"].tolist() == ["202606", "202606"]


def test_previous_day_volume_can_roll_next_trading_day() -> None:
    frame = bars(
        [
            ("2026-06-17 08:45", "202606", 100, 100),
            ("2026-06-17 08:45", "202607", 200, 10),
            ("2026-06-18 08:45", "202606", 101, 10),
            ("2026-06-18 08:45", "202607", 201, 100),
            ("2026-06-19 08:45", "202606", 102, 10),
            ("2026-06-19 08:45", "202607", 202, 100),
        ]
    )

    result = select_contract_bars(frame, contract_mode="continuous_front_month")

    assert result.audit["selected_contract"].tolist() == ["202606", "202606", "202607"]
    assert result.audit.loc[2, "decision_source_date"] == pd.Timestamp("2026-06-18").date()
    assert result.audit.loc[2, "roll_effective_date"] == pd.Timestamp("2026-06-19").date()


def test_confirmation_is_based_only_on_completed_days() -> None:
    frame = bars(
        [
            ("2026-06-17 08:45", "202606", 100, 10),
            ("2026-06-17 08:45", "202607", 200, 100),
            ("2026-06-18 08:45", "202606", 101, 10),
            ("2026-06-18 08:45", "202607", 201, 100),
            ("2026-06-19 08:45", "202606", 102, 10),
            ("2026-06-19 08:45", "202607", 202, 100),
        ]
    )

    result = select_contract_bars(
        frame, contract_mode="continuous_front_month", roll_confirmation_days=2
    )

    assert result.audit["selected_contract"].tolist() == ["202606", "202606", "202607"]
    assert result.audit.loc[1, "confirmation_count"] == 1
    assert result.audit.loc[2, "confirmation_count"] == 2


def test_roll_effective_date_is_after_decision_date() -> None:
    frame = bars(
        [
            ("2026-06-17 08:45", "202606", 100, 10),
            ("2026-06-17 08:45", "202607", 200, 100),
            ("2026-06-18 08:45", "202606", 101, 10),
            ("2026-06-18 08:45", "202607", 201, 100),
        ]
    )

    result = select_contract_bars(frame, contract_mode="continuous_front_month")
    rolled = result.audit.loc[result.audit["rolled"]].iloc[0]

    assert rolled["roll_effective_date"] > rolled["decision_source_date"]


def test_invalid_contract_is_reported_and_not_mixed() -> None:
    frame = bars(
        [
            ("2026-06-17 08:45", "202606W1", 90, 100),
            ("2026-06-17 08:45", "202606", 100, 10),
        ]
    )

    result = select_contract_bars(frame, contract_mode="continuous_front_month")

    assert result.invalid_contracts == ("202606W1",)
    assert set(result.bars["contract"]) == {"202606"}


def test_duplicate_timestamp_within_selected_contract_is_rejected() -> None:
    frame = bars(
        [
            ("2026-06-17 08:45", "202606", 100, 1),
            ("2026-06-17 08:45", "202606", 101, 2),
        ]
    )

    with pytest.raises(ValueError, match="duplicate active timestamps"):
        select_contract_bars(frame, contract_mode="continuous_front_month")


def test_contract_selection_is_deterministic() -> None:
    frame = bars(
        [
            ("2026-06-17 08:45", "202607", 200, 10),
            ("2026-06-17 08:45", "202606", 100, 100),
        ]
    )

    first = select_contract_bars(frame, contract_mode="continuous_front_month")
    second = select_contract_bars(
        frame.sample(frac=1, random_state=4),
        contract_mode="continuous_front_month",
    )

    pd.testing.assert_frame_equal(first.bars, second.bars)
    pd.testing.assert_frame_equal(first.audit, second.audit)


def test_indicators_reset_at_contract_segment() -> None:
    frame = bars(
        [
            ("2026-06-17 08:45", "202606", 100, 1),
            ("2026-06-17 08:50", "202606", 101, 1),
            ("2026-06-18 08:45", "202607", 500, 1),
            ("2026-06-18 08:50", "202607", 501, 1),
        ]
    )
    frame["contract_segment_id"] = ["segment_001", "segment_001", "segment_002", "segment_002"]

    result = append_basic_indicators(
        frame,
        ema_fast=2,
        ema_slow=2,
        atr_period=2,
        volatility_window=2,
    )

    assert np.isnan(result.loc[0, "ema_fast"])
    assert np.isnan(result.loc[2, "ema_fast"])
    assert result.loc[3, "ema_fast"] == pytest.approx(500 + (2 / 3))
    assert np.isnan(result.loc[2, "atr"])
    assert result.loc[3, "atr"] == 2.0


def test_strategy_previous_row_does_not_cross_segment() -> None:
    frame = bars(
        [
            ("2026-06-17 09:00", "202606", 99, 1),
            ("2026-06-18 09:00", "202607", 110, 1),
        ]
    )
    frame["contract_segment_id"] = ["segment_001", "segment_002"]
    frame["ema_fast"] = [100.0, 105.0]
    frame["ema_slow"] = [98.0, 100.0]
    frame["vwap"] = [98.0, 100.0]
    frame["atr"] = [20.0, 20.0]

    signals = VWAPTrendStrategy().generate_signals(frame)

    assert signals["side"].tolist() == ["HOLD", "HOLD"]
