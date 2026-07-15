from __future__ import annotations

import pytest

from aiquanttrader.backtest import XAUUSD_ICMARKETS_DEMO


def test_xauusd_icmarkets_demo_contract_economics():
    spec = XAUUSD_ICMARKETS_DEMO

    assert spec.point_size == pytest.approx(0.01)
    assert spec.price_value_per_lot == pytest.approx(100.0)
    assert spec.commission_per_side_lot == pytest.approx(3.5)

    fills = spec.fill_config()
    assert fills.point_size == pytest.approx(0.01)
    assert fills.spread_points == pytest.approx(5.0)
    assert fills.commission_per_lot == pytest.approx(3.5)
