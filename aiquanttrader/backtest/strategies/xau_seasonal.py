"""Seasonal/calendar strategy for XAUUSD.

Exploits time-of-day and day-of-week patterns:
- London open momentum (07:00-08:00 UTC)
- NY open momentum (13:00-14:00 UTC)
- Asian session mean reversion (00:00-06:00 UTC)
- Day-of-week effects
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aiquanttrader.backtest.strategies.base import Signal, Strategy


class XauSeasonal(Strategy):
    """Calendar-based seasonal patterns in XAUUSD."""

    name = "XauSeasonal"

    def __init__(
        self,
        *,
        london_open_hour: int = 7,
        ny_open_hour: int = 13,
        sl_atr_mult: float = 1.0,
        tp_atr_mult: float = 1.5,
        min_momentum_pct: float = 0.0008,
    ):
        self.london_open_hour = london_open_hour
        self.ny_open_hour = ny_open_hour
        self.sl_atr_mult = sl_atr_mult
        self.tp_atr_mult = tp_atr_mult
        self.min_momentum_pct = min_momentum_pct
        self._session_high: float = 0.0
        self._session_low: float = float("inf")
        self._last_session_hour: int = -1

    def on_bar(self, **kwargs: Any) -> Signal:
        bar = kwargs.get("bar")
        indicators = kwargs.get("indicators")

        if bar is None or indicators is None:
            return Signal(action="HOLD", strategy=self.name)

        timestamp = int(bar.get("time", 0))
        if timestamp == 0:
            return Signal(action="HOLD", strategy=self.name)

        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        hour = dt.hour
        day = dt.weekday()  # 0=Monday

        atr = indicators.get("atr_14", 0.0)
        close = float(bar.get("close", 0))
        high = float(bar.get("high", 0))
        low = float(bar.get("low", 0))

        if atr == 0 or close == 0:
            return Signal(action="HOLD", strategy=self.name)

        sl_dist = atr * self.sl_atr_mult
        tp_dist = atr * self.tp_atr_mult

        # Avoid Friday afternoon (liquidity thins, spreads widen)
        if day == 4 and hour >= 16:
            return Signal(action="HOLD", strategy=self.name)

        # London open breakout (07:00-08:00 UTC)
        if hour == self.london_open_hour:
            momentum = indicators.get("ret_5", 0.0)
            if momentum and abs(momentum) > self.min_momentum_pct:
                action = "BUY" if momentum > 0 else "SELL"
                return Signal(
                    action=action,
                    sl_distance=sl_dist,
                    tp_distance=tp_dist,
                    strategy=self.name,
                    comment=f"Seasonal_london_{action.lower()}",
                )

        # NY open breakout (13:00-14:00 UTC)
        if hour == self.ny_open_hour:
            momentum = indicators.get("ret_5", 0.0)
            if momentum and abs(momentum) > self.min_momentum_pct:
                action = "BUY" if momentum > 0 else "SELL"
                return Signal(
                    action=action,
                    sl_distance=sl_dist,
                    tp_distance=tp_dist,
                    strategy=self.name,
                    comment=f"Seasonal_ny_{action.lower()}",
                )

        return Signal(action="HOLD", strategy=self.name)
