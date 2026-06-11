"""Buy-and-hold smoke strategy for engine validation."""

from __future__ import annotations

from finrobot.backtest.position import Position
from finrobot.backtest.strategies.base import Signal, Strategy


class BuyAndHold(Strategy):
    """Buy on the first bar, then hold until the engine liquidates at the end."""

    name = "BuyAndHold"

    def __init__(self, *, risk_per_trade_fraction: float = 0.001):
        self.risk_per_trade_fraction = float(risk_per_trade_fraction)
        self.bought = False

    def on_bar(
        self,
        *,
        idx: int,
        bar: dict,
        history: list[dict],
        open_positions: list[Position],
        equity: float,
        day_closed_pnl: float,
    ) -> Signal:
        """Return one BUY signal on the first observed bar, then HOLD."""

        if self.bought:
            return Signal(action="HOLD", strategy=self.name)
        self.bought = True
        return Signal(action="BUY", sl_distance=None, tp_distance=None, strategy=self.name)
