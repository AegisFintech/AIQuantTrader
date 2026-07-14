"""Broker instrument economics used by deterministic backtests."""

from __future__ import annotations

from dataclasses import dataclass

from finrobot.backtest.fills import FillConfig


@dataclass(frozen=True)
class InstrumentSpec:
    """Cash and quote-unit assumptions for one broker instrument."""

    symbol: str
    point_size: float
    tick_size: float
    tick_value: float
    spread_points: float
    commission_per_side_lot: float
    slippage_points: float = 0.0
    swap_per_lot_per_day: float = 0.0

    @property
    def price_value_per_lot(self) -> float:
        """Return cash PnL for a one-unit price move at one lot."""

        if self.tick_size <= 0.0 or self.tick_value <= 0.0:
            raise ValueError("tick_size and tick_value must be positive")
        return self.tick_value / self.tick_size

    def fill_config(self) -> FillConfig:
        return FillConfig(
            point_size=self.point_size,
            spread_points=self.spread_points,
            slippage_points=self.slippage_points,
            commission_per_lot=self.commission_per_side_lot,
            swap_per_lot_per_day=self.swap_per_lot_per_day,
        )


# Verified from ICMarketsSC-Demo status/deals on 2026-07-14. Recheck when the
# account, broker contract, or symbol specification changes.
XAUUSD_ICMARKETS_DEMO = InstrumentSpec(
    symbol="XAUUSD",
    point_size=0.01,
    tick_size=0.01,
    tick_value=1.0,
    spread_points=5.0,
    commission_per_side_lot=3.5,
)
