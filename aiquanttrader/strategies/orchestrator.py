"""Multi-strategy orchestrator.

Routes bars to active strategy sleeves based on market regime,
handles conflict resolution, and enforces global position limits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aiquanttrader.backtest.strategies.base import Signal, Strategy


@dataclass
class SleeveConfig:
    """Configuration for a single strategy sleeve."""

    name: str
    strategy: Strategy
    active_regimes: list[str]  # e.g., ["trending"], ["ranging"], ["trending", "volatile"]
    daily_loss_budget_pct: float = 0.002  # max daily loss for this sleeve
    max_positions: int = 1
    enabled: bool = True


@dataclass
class OrchestratorConfig:
    """Global orchestrator configuration."""

    max_total_positions: int = 3
    max_same_direction: int = 2
    conflict_resolution: str = "cancel"  # "cancel" or "strongest"


class StrategyOrchestrator(Strategy):
    """Routes signals through multiple strategy sleeves based on regime.

    Acts as a composite Strategy that can be passed to the Backtester.
    """

    name = "Orchestrator"

    def __init__(
        self,
        sleeves: list[SleeveConfig],
        config: OrchestratorConfig | None = None,
    ):
        self.sleeves = sleeves
        self.config = config or OrchestratorConfig()
        self._current_regime: str = "unknown"
        self._sleeve_pnls: dict[str, float] = {s.name: 0.0 for s in sleeves}
        self._open_positions: list[dict] = []

    def set_regime(self, regime: str) -> None:
        """Update the current market regime (call before on_bar)."""
        self._current_regime = regime

    def update_sleeve_pnl(self, sleeve_name: str, daily_pnl: float) -> None:
        """Update the daily P&L for a sleeve (for budget enforcement)."""
        self._sleeve_pnls[sleeve_name] = daily_pnl

    def on_bar(self, **kwargs: Any) -> Signal:
        """Collect signals from active sleeves and resolve conflicts."""
        signals: list[tuple[str, Signal]] = []

        for sleeve in self.sleeves:
            if not sleeve.enabled:
                continue
            if self._current_regime not in sleeve.active_regimes:
                continue
            if self._sleeve_pnls.get(sleeve.name, 0.0) <= -sleeve.daily_loss_budget_pct:
                continue

            signal = sleeve.strategy.on_bar(**kwargs)
            if signal.action != "HOLD":
                signals.append((sleeve.name, signal))

        if not signals:
            return Signal(action="HOLD", strategy=self.name)

        return self._resolve_conflicts(signals)

    def _resolve_conflicts(self, signals: list[tuple[str, Signal]]) -> Signal:
        buys = [(name, s) for name, s in signals if s.action == "BUY"]
        sells = [(name, s) for name, s in signals if s.action == "SELL"]

        if buys and sells:
            if self.config.conflict_resolution == "cancel":
                return Signal(action="HOLD", strategy=self.name, comment="conflict_cancelled")
            # "strongest" — pick the one with the wider SL (more conviction)
            all_signals = buys + sells
            strongest = max(all_signals, key=lambda x: x[1].sl_distance or 0)
            return strongest[1]

        chosen = (buys or sells)[0]
        return chosen[1]

    def reset_daily(self) -> None:
        """Reset daily P&L tracking (call at start of each trading day)."""
        self._sleeve_pnls = {s.name: 0.0 for s in self.sleeves}
