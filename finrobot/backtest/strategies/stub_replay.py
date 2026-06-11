"""Replay strategy for EA parity checks."""

from __future__ import annotations

from finrobot.backtest.position import Position
from finrobot.backtest.strategies.base import Signal, Strategy


class StubReplayStrategy(Strategy):
    """Emit audited EA decisions at their recorded bar indices."""

    name = "StubReplay"

    def __init__(self, decisions: list[dict]):
        self._by_bar = {
            int(d["bar_idx"]): d for d in decisions if d.get("bar_idx") is not None
        }

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
        """Return the audited signal for ``idx`` or HOLD when none exists."""

        decision = self._by_bar.get(idx)
        if decision is None:
            return Signal(action="HOLD", strategy=self.name)
        return Signal(
            action=str(decision["action"]).upper(),
            sl_distance=decision.get("sl_distance"),
            tp_distance=decision.get("tp_distance"),
            strategy=self.name,
            comment=f"replay idx={idx}",
        )
