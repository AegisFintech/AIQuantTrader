"""Mean reversion strategy for ranging XAUUSD markets.

Active when ADX < 20 (no trend). Enters on RSI extremes + Bollinger band
touch + PDA zone confirmation. Tight stops, quick targets.
"""

from __future__ import annotations

from typing import Any

from finrobot.backtest.strategies.base import Signal, Strategy


class XauMeanReversion(Strategy):
    """Mean reversion on XAUUSD — active in ranging regimes only."""

    name = "XauMeanReversion"

    def __init__(
        self,
        *,
        rsi_oversold: float = 28.0,
        rsi_overbought: float = 72.0,
        bb_lower_threshold: float = 0.05,
        bb_upper_threshold: float = 0.95,
        adx_max: float = 20.0,
        sl_atr_mult: float = 0.8,
        tp_atr_mult: float = 1.2,
        pda_discount_max: float = 0.35,
        pda_premium_min: float = 0.65,
    ):
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.bb_lower_threshold = bb_lower_threshold
        self.bb_upper_threshold = bb_upper_threshold
        self.adx_max = adx_max
        self.sl_atr_mult = sl_atr_mult
        self.tp_atr_mult = tp_atr_mult
        self.pda_discount_max = pda_discount_max
        self.pda_premium_min = pda_premium_min
        self._state: _State | None = None

    def on_bar(self, **kwargs: Any) -> Signal:
        bar = kwargs.get("bar")
        indicators = kwargs.get("indicators")

        if bar is None or indicators is None:
            return Signal(action="HOLD", strategy=self.name)

        rsi = indicators.get("rsi_14", 50.0)
        bb_pct = indicators.get("bb_pct_b", 0.5)
        adx = indicators.get("adx_14", 25.0)
        atr = indicators.get("atr_14", 0.0)
        pda = indicators.get("pda", 0.5)

        if adx > self.adx_max or atr == 0:
            return Signal(action="HOLD", strategy=self.name)

        sl_dist = atr * self.sl_atr_mult
        tp_dist = atr * self.tp_atr_mult

        # Long: RSI oversold + at/below lower Bollinger + in discount
        if rsi <= self.rsi_oversold and bb_pct <= self.bb_lower_threshold and pda <= self.pda_discount_max:
            return Signal(
                action="BUY",
                sl_distance=sl_dist,
                tp_distance=tp_dist,
                strategy=self.name,
                comment="MeanRev_long_rsi_bb",
            )

        # Short: RSI overbought + at/above upper Bollinger + in premium
        if rsi >= self.rsi_overbought and bb_pct >= self.bb_upper_threshold and pda >= self.pda_premium_min:
            return Signal(
                action="SELL",
                sl_distance=sl_dist,
                tp_distance=tp_dist,
                strategy=self.name,
                comment="MeanRev_short_rsi_bb",
            )

        return Signal(action="HOLD", strategy=self.name)


class _State:
    pass
