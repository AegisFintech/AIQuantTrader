"""Strategy interface for the bar-by-bar backtester."""

from __future__ import annotations

from dataclasses import dataclass

from aiquanttrader.backtest.position import Position


@dataclass(frozen=True)
class Signal:
    """A strategy decision for the current bar."""

    action: str
    sl_distance: float | None = None
    tp_distance: float | None = None
    strategy: str = ""
    comment: str = ""
    smc_score: int | None = None


class Strategy:
    """Base class for deterministic backtest strategies."""

    name: str = ""

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
        """Return the strategy signal for the current bar."""

        raise NotImplementedError
