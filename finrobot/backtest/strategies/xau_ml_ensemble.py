"""ML ensemble strategy for XAUUSD.

Uses LightGBM model probability output to generate signals.
Only trades when model confidence exceeds threshold.
Optionally requires SMC confirmation for additional filtering.
"""

from __future__ import annotations

from typing import Any

from finrobot.backtest.strategies.base import Signal, Strategy


class XauMLEnsemble(Strategy):
    """ML-driven signal generation with confidence gating."""

    name = "XauMLEnsemble"

    def __init__(
        self,
        *,
        model: Any = None,
        threshold_long: float = 0.65,
        threshold_short: float = 0.35,
        require_smc_confirm: bool = True,
        smc_min_score: int = 2,
        sl_atr_mult: float = 1.2,
        tp_atr_mult: float = 2.4,
    ):
        self.model = model
        self.threshold_long = threshold_long
        self.threshold_short = threshold_short
        self.require_smc_confirm = require_smc_confirm
        self.smc_min_score = smc_min_score
        self.sl_atr_mult = sl_atr_mult
        self.tp_atr_mult = tp_atr_mult

    def on_bar(self, **kwargs: Any) -> Signal:
        indicators = kwargs.get("indicators")
        if indicators is None:
            return Signal(action="HOLD", strategy=self.name)

        ml_prob = indicators.get("ml_probability", 0.5)
        atr = indicators.get("atr_14", 0.0)
        smc_long = indicators.get("smc_long_score", 0)
        smc_short = indicators.get("smc_short_score", 0)

        if atr == 0:
            return Signal(action="HOLD", strategy=self.name)

        sl_dist = atr * self.sl_atr_mult
        tp_dist = atr * self.tp_atr_mult

        # Long signal: high probability of up move
        if ml_prob >= self.threshold_long:
            if self.require_smc_confirm and smc_long < self.smc_min_score:
                return Signal(action="HOLD", strategy=self.name)
            return Signal(
                action="BUY",
                sl_distance=sl_dist,
                tp_distance=tp_dist,
                strategy=self.name,
                comment=f"ML_long_p={ml_prob:.2f}",
            )

        # Short signal: high probability of down move
        if ml_prob <= self.threshold_short:
            if self.require_smc_confirm and smc_short < self.smc_min_score:
                return Signal(action="HOLD", strategy=self.name)
            return Signal(
                action="SELL",
                sl_distance=sl_dist,
                tp_distance=tp_dist,
                strategy=self.name,
                comment=f"ML_short_p={ml_prob:.2f}",
            )

        return Signal(action="HOLD", strategy=self.name)
