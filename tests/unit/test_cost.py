from __future__ import annotations

import pytest

from tifq.backtest import CostModel, apply_slippage, calculate_order_cost


def test_apply_slippage_adjusts_buy_and_sell_prices_conservatively() -> None:
    assert apply_slippage(100.0, "BUY", 1.0) == 101.0
    assert apply_slippage(100.0, "SELL", 1.0) == 99.0


def test_apply_slippage_rejects_negative_slippage() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        apply_slippage(100.0, "BUY", -1.0)


def test_calculate_order_cost_applies_commission_tax_and_slippage_per_side() -> None:
    model = CostModel(
        point_value=10,
        commission_per_side=5,
        tax_rate=0.00002,
        slippage_points_per_side=1,
    )

    cost = calculate_order_cost(22001.0, 2, model)

    assert cost.fee == 10
    assert cost.tax == pytest.approx(22001.0 * 10 * 0.00002 * 2)
    assert cost.slippage == 20
    assert cost.total == pytest.approx(cost.fee + cost.tax + cost.slippage)
