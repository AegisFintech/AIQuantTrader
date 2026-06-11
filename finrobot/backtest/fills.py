"""Deterministic fill simulation for bar-based backtests."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class FillConfig:
    """Execution assumptions for deterministic simulated fills."""

    spread_points: float = 5.0
    slippage_points: float = 0.0
    commission_per_lot: float = 0.0
    swap_per_lot_per_day: float = 0.0


class FillModel:
    """Small object wrapper around :func:`simulate_fill`."""

    def __init__(self, config: FillConfig | None = None):
        self.config = config or FillConfig()

    def simulate(
        self,
        *,
        side: str,
        intended_price: float,
        bar_high: float,
        bar_low: float,
    ) -> tuple[float, float, float]:
        """Return the deterministic fill tuple using this model's config."""

        return simulate_fill(
            side=side,
            intended_price=intended_price,
            bar_high=bar_high,
            bar_low=bar_low,
            config=self.config,
        )


def simulate_fill(
    *,
    side: str,
    intended_price: float,
    bar_high: float,
    bar_low: float,
    config: FillConfig,
) -> tuple[float, float, float]:
    """Return ``(fill_price, slippage_applied, commission_charged)``.

    BUY fills pay half-spread plus deterministic slippage; SELL fills receive
    half-spread less deterministic slippage. If that synthetic price falls
    outside the bar's high-low range, the fill is clamped to the nearest bound
    and the clamp distance is included as an adverse slippage penalty.
    """

    side_normalized = side.upper()
    if side_normalized not in {"BUY", "SELL"}:
        raise ValueError(f"side must be BUY or SELL, got {side!r}")
    intended = _finite_float("intended_price", intended_price)
    high = _finite_float("bar_high", bar_high)
    low = _finite_float("bar_low", bar_low)
    if high < low:
        raise ValueError(f"bar_high must be >= bar_low, got {high} < {low}")

    half_spread = float(config.spread_points) / 2.0
    slippage = float(config.slippage_points)
    if side_normalized == "BUY":
        raw_fill = intended + half_spread + slippage
        adverse_slippage = raw_fill - intended
    else:
        raw_fill = intended - half_spread - slippage
        adverse_slippage = intended - raw_fill

    fill_price = min(max(raw_fill, low), high)
    clamp_penalty = abs(raw_fill - fill_price)
    slippage_applied = adverse_slippage + clamp_penalty
    return fill_price, slippage_applied, float(config.commission_per_lot)


def _finite_float(name: str, value: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric, got {value!r}") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return result
